from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from app.config import settings
from app.db import acquire, close_pool, pool
from app.models import (
    ApprovalDecision,
    ConversationCreate,
    FeedbackCreate,
    HandlingPlanRequest,
    KpiQuery,
    LoginRequest,
    MessageCreate,
    TicketActionCreate,
)
from app.handling_plan import build_handling_plan_card
from app.security import (
    Principal,
    create_access_token,
    current_principal,
    ensure_demo_users,
    require_roles,
    verify_password,
)
from observability.runtime import (
    TelemetryConfig,
    configure_telemetry,
    current_trace_id,
    force_flush,
    instrument_fastapi_app,
    traced_span,
)

logger = logging.getLogger(__name__)


def _record(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "items"):
        return {key: _record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_record(item) for item in value]
    return value


async def _verify_product_schema() -> None:
    db_pool = await pool()
    async with db_pool.acquire() as conn:
        required = {
            "app_user",
            "support_conversation",
            "support_message",
            "copilot_feedback",
            "product_audit_event",
            "hitl_approval_request",
            "agent_action_lineage",
        }
        existing = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
            sorted(required),
        )
        existing_names = {row["tablename"] for row in existing}
        missing = sorted(required - existing_names)
        if missing:
            raise RuntimeError(f"database migrations are incomplete; missing tables: {', '.join(missing)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _verify_product_schema()
    if settings.enable_demo_users:
        await ensure_demo_users()
    yield
    await close_pool()
    force_flush()


configure_telemetry(
    TelemetryConfig(
        service_name=settings.otel_service_name,
        release_id=settings.release_id,
        environment=settings.otel_environment,
        endpoint=settings.otel_exporter_otlp_endpoint,
        project_name=settings.otel_project_name,
        enabled=settings.otel_enabled,
        sample_ratio=settings.otel_sample_ratio,
    )
)
HTTPXClientInstrumentor().instrument()

app = FastAPI(
    title="OmniSupport Copilot Product API",
    description="Enterprise support workspace control plane",
    version="1.0.0",
    lifespan=lifespan,
)
instrument_fastapi_app(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8010", "http://127.0.0.1:8010"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if trace_id := current_trace_id():
        response.headers["X-Trace-ID"] = trace_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    logger.exception(
        "Unhandled Product API error request_id=%s",
        getattr(request.state, "request_id", None),
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "The request could not be completed.",
            "request_id": getattr(request.state, "request_id", None),
            "release_id": settings.release_id,
        },
    )


async def _audit(
    principal: Principal | None,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_audit_event (
                event_id, tenant_id, actor_id, actor_role, event_type, resource_type,
                resource_id, outcome, request_id, trace_id, release_id, details
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            """,
            f"evt_{uuid.uuid4().hex}",
            principal.tenant_id if principal else settings.demo_tenant_id,
            principal.user_id if principal else None,
            principal.role if principal else None,
            event_type,
            resource_type,
            resource_id,
            outcome,
            request_id,
            trace_id or current_trace_id(),
            settings.release_id,
            details or {},
        )


async def _service_get(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=4.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


@app.get("/live", include_in_schema=False)
async def live() -> dict[str, str]:
    """Cheap container liveness probe; readiness remains available at /health."""
    return {"status": "ok", "service": "copilot_api"}


@app.get("/health")
async def health() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        async with acquire() as conn:
            checks["postgres"] = "ok" if await conn.fetchval("SELECT 1") == 1 else "down"
    except Exception:
        checks["postgres"] = "down"
    for name, url in (
        ("rag_api", f"{settings.rag_api_url}/health"),
        ("tool_api", f"{settings.tool_api_url}/health"),
    ):
        try:
            checks[name] = (await _service_get(url)).get("status", "ok")
        except Exception:
            checks[name] = "down"
    overall = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return {"status": overall, "service": "copilot_api", "release_id": settings.release_id, "checks": checks}


@app.post("/api/v1/auth/login")
async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, tenant_id, email, display_name, role, password_hash
            FROM app_user WHERE lower(email) = lower($1) AND active = TRUE
            """,
            payload.email,
        )
        if row is None or not verify_password(payload.password, row["password_hash"]):
            await _audit(
                None,
                event_type="auth.login",
                resource_type="user",
                resource_id=None,
                outcome="denied",
                request_id=request.state.request_id,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
        principal = Principal(**{key: row[key] for key in ("user_id", "tenant_id", "email", "display_name", "role")})
        await conn.execute("UPDATE app_user SET last_login_at = NOW() WHERE user_id = $1", principal.user_id)
    await _audit(
        principal,
        event_type="auth.login",
        resource_type="user",
        resource_id=principal.user_id,
        outcome="success",
        request_id=request.state.request_id,
    )
    return {
        "access_token": create_access_token(principal),
        "token_type": "bearer",
        "expires_in": settings.auth_token_ttl_seconds,
        "user": principal.__dict__,
    }


@app.get("/api/v1/me")
async def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return principal.__dict__


@app.get("/api/v1/cases")
async def list_cases(
    status_filter: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    search: str | None = None,
    limit: int = Query(default=40, ge=1, le=200),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    conditions = ["t.tenant_id = $1"]
    values: list[Any] = [principal.tenant_id]
    if status_filter:
        values.append(status_filter)
        conditions.append(f"t.status::text = ${len(values)}")
    if priority:
        values.append(priority)
        conditions.append(f"t.priority::text = ${len(values)}")
    if search:
        values.append(f"%{search}%")
        conditions.append(f"(t.ticket_id ILIKE ${len(values)} OR t.subject ILIKE ${len(values)})")
    where_sql = " AND ".join(conditions)
    filter_values = list(values)
    values.append(limit)
    async with acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM ticket_fact t WHERE {where_sql}",
            *filter_values,
        )
        rows = await conn.fetch(
            f"""
            SELECT t.ticket_id, t.status::text, t.priority::text, t.category,
                   t.product_line::text, t.subject, t.assignee_id, t.sla_due_at,
                   t.created_at, t.updated_at, t.org_id, c.org_name, c.sla_tier::text,
                   COALESCE((SELECT COUNT(*) FROM support_message m
                     JOIN support_conversation sc ON sc.conversation_id = m.conversation_id
                     WHERE sc.ticket_id = t.ticket_id), 0) AS message_count
            FROM ticket_fact t
            LEFT JOIN customer_dim c ON c.customer_id = t.customer_id
            WHERE {where_sql}
            ORDER BY
                CASE t.priority::text WHEN 'p1_critical' THEN 1 WHEN 'p2_high' THEN 2
                     WHEN 'p3_medium' THEN 3 ELSE 4 END,
                COALESCE(t.updated_at, t.created_at) DESC
            LIMIT ${len(values)}
            """,
            *values,
        )
    return {
        "items": [_record(dict(row)) for row in rows],
        "count": int(total),
        "page_count": len(rows),
    }


async def _case_row(ticket_id: str, principal: Principal):
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.*, t.status::text AS status_text, t.priority::text AS priority_text,
                   t.product_line::text AS product_line_text, t.sla_tier::text AS sla_tier_text,
                   c.org_name
            FROM ticket_fact t
            LEFT JOIN customer_dim c ON c.customer_id = t.customer_id
            WHERE t.ticket_id = $1 AND t.tenant_id = $2
            """,
            ticket_id,
            principal.tenant_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="case_not_found")
    return row


@app.get("/api/v1/cases/{ticket_id}")
async def get_case(ticket_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    row = await _case_row(ticket_id, principal)
    async with acquire() as conn:
        comments = await conn.fetch(
            """
            SELECT comment_id, author_id, author_role, body, created_at
            FROM ticket_comment_fact WHERE ticket_id = $1 ORDER BY created_at DESC LIMIT 20
            """,
            ticket_id,
        )
        conversations = await conn.fetch(
            """
            SELECT conversation_id, title, status, created_by, created_at, updated_at
            FROM support_conversation
            WHERE ticket_id = $1 AND tenant_id = $2 ORDER BY updated_at DESC
            """,
            ticket_id,
            principal.tenant_id,
        )
    case = dict(row)
    case["status"] = case.pop("status_text")
    case["priority"] = case.pop("priority_text")
    case["product_line"] = case.pop("product_line_text")
    case["sla_tier"] = case.pop("sla_tier_text")
    return {
        "case": _record(case),
        "comments": [_record(dict(item)) for item in comments],
        "conversations": [_record(dict(item)) for item in conversations],
    }


@app.post("/api/v1/cases/{ticket_id}/conversations", status_code=201)
async def create_conversation(
    ticket_id: str,
    payload: ConversationCreate,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    case = await _case_row(ticket_id, principal)
    conversation_id = f"conv_{uuid.uuid4().hex}"
    title = payload.title or f"Copilot: {case['subject'][:120]}"
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO support_conversation (
                conversation_id, tenant_id, ticket_id, title, created_by
            ) VALUES ($1,$2,$3,$4,$5)
            RETURNING *
            """,
            conversation_id,
            principal.tenant_id,
            ticket_id,
            title,
            principal.user_id,
        )
    return _record(dict(row))


async def _conversation(conversation_id: str, principal: Principal):
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT sc.*, t.product_line::text AS product_line, t.subject, t.status::text AS ticket_status
            FROM support_conversation sc
            JOIN ticket_fact t ON t.ticket_id = sc.ticket_id AND t.tenant_id = sc.tenant_id
            WHERE sc.conversation_id = $1 AND sc.tenant_id = $2
            """,
            conversation_id,
            principal.tenant_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return row


@app.get("/api/v1/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    await _conversation(conversation_id, principal)
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM support_message WHERE conversation_id = $1 ORDER BY created_at",
            conversation_id,
        )
    return {"items": [_record(dict(row)) for row in rows]}


@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=201)
async def ask_copilot(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    conversation = await _conversation(conversation_id, principal)
    user_message_id = f"msg_{uuid.uuid4().hex}"
    started = time.perf_counter()
    async with acquire() as conn:
        await conn.execute(
            """
            INSERT INTO support_message (
                message_id, conversation_id, tenant_id, actor_id, role, content
            ) VALUES ($1,$2,$3,$4,'user',$5)
            """,
            user_message_id,
            conversation_id,
            principal.tenant_id,
            principal.user_id,
            payload.question,
        )

    rag_request = {
        "question": payload.question,
        "tenant_id": principal.tenant_id,
        "product_line": conversation["product_line"],
        "actor_role": principal.role,
        "visibility_scope": "internal",
        "top_k": 5,
        "retrieval_mode": payload.retrieval_mode,
        "include_debug": payload.include_debug,
    }
    with traced_span(
        "product.copilot.answer",
        kind="CHAIN",
        attributes={
            "omni.conversation_id": conversation_id,
            "omni.ticket_id": conversation["ticket_id"],
            "omni.actor.role": principal.role,
        },
    ):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{settings.rag_api_url}/rag/answer",
                    json=rag_request,
                    headers={
                        "X-Service-Token": settings.internal_service_token,
                        "X-Actor-ID": principal.user_id,
                        "X-Actor-Role": principal.role,
                        "X-Tenant-ID": principal.tenant_id,
                        "X-Request-ID": request.state.request_id,
                    },
                )
                response.raise_for_status()
                answer = response.json()
        except httpx.HTTPError as exc:
            await _audit(
                principal,
                event_type="copilot.answer",
                resource_type="conversation",
                resource_id=conversation_id,
                outcome="dependency_failed",
                request_id=request.state.request_id,
                details={"dependency": "rag_api", "error_type": type(exc).__name__},
            )
            raise HTTPException(status_code=502, detail="rag_api_unavailable") from exc

    assistant_message_id = f"msg_{uuid.uuid4().hex}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    citations = answer.get("citations", [])
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO support_message (
                message_id, conversation_id, tenant_id, role, content, citations,
                evidence_ids, confidence, abstain_reason, trace_id, release_id,
                data_release_id, index_release_id, prompt_release_id, graph_release_id,
                latency_ms, generation_mode, generation_provider, generation_model
            ) VALUES (
                $1,$2,$3,'assistant',$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
                $16,$17,$18
            )
            RETURNING *
            """,
            assistant_message_id,
            conversation_id,
            principal.tenant_id,
            answer["answer"],
            citations,
            answer.get("evidence_ids", []),
            answer.get("confidence"),
            answer.get("abstain_reason"),
            answer.get("trace_id"),
            answer.get("release_id"),
            answer.get("data_release_id"),
            answer.get("index_release_id"),
            answer.get("prompt_release_id"),
            answer.get("graph_release_id"),
            latency_ms,
            answer.get("generation_mode"),
            answer.get("generation_provider"),
            answer.get("generation_model"),
        )
        await conn.execute(
            "UPDATE support_conversation SET updated_at = NOW() WHERE conversation_id = $1",
            conversation_id,
        )
    await _audit(
        principal,
        event_type="copilot.answer",
        resource_type="message",
        resource_id=assistant_message_id,
        outcome="abstained" if answer.get("abstain_reason") else "success",
        request_id=request.state.request_id,
        trace_id=answer.get("trace_id"),
        details={
            "ticket_id": conversation["ticket_id"],
            "evidence_count": len(answer.get("evidence_ids", [])),
            "confidence": answer.get("confidence"),
            "generation_mode": answer.get("generation_mode"),
            "generation_provider": answer.get("generation_provider"),
            "generation_model": answer.get("generation_model"),
            "latency_ms": latency_ms,
        },
    )
    result = _record(dict(row))
    if payload.include_debug:
        result["retrieval_debug"] = answer.get("retrieval_debug")
        result["graph_debug"] = answer.get("graph_debug")
    return result


@app.post("/api/v1/handling-plan", status_code=200)
async def create_handling_plan(
    payload: HandlingPlanRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Return a structured problem-handling plan card grounded on RAG evidence."""
    rag_request = {
        "question": payload.question,
        "tenant_id": principal.tenant_id,
        "product_line": payload.product_line or "northstar_workspace",
        "actor_role": principal.role,
        "visibility_scope": "internal",
        "top_k": 5,
        "retrieval_mode": payload.retrieval_mode,
        "include_debug": payload.include_debug,
    }
    with traced_span(
        "product.handling_plan",
        kind="CHAIN",
        attributes={
            "omni.theme": "webhook-troubleshooting",
            "omni.actor.role": principal.role,
            "omni.ticket_id": payload.ticket_id or "",
        },
    ):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{settings.rag_api_url}/rag/answer",
                    json=rag_request,
                    headers={
                        "X-Service-Token": settings.internal_service_token,
                        "X-Actor-ID": principal.user_id,
                        "X-Actor-Role": principal.role,
                        "X-Tenant-ID": principal.tenant_id,
                        "X-Request-ID": request.state.request_id,
                    },
                )
                response.raise_for_status()
                answer = response.json()
        except httpx.HTTPError as exc:
            await _audit(
                principal,
                event_type="copilot.handling_plan",
                resource_type="handling_plan",
                resource_id=payload.ticket_id or "none",
                outcome="dependency_failed",
                request_id=request.state.request_id,
                details={"dependency": "rag_api", "error_type": type(exc).__name__},
            )
            raise HTTPException(status_code=502, detail="rag_api_unavailable") from exc

    card = build_handling_plan_card(question=payload.question, rag=answer)
    await _audit(
        principal,
        event_type="copilot.handling_plan",
        resource_type="handling_plan",
        resource_id=payload.ticket_id or card.get("trace_id") or "none",
        outcome="abstained" if card.get("abstain_reason") else "success",
        request_id=request.state.request_id,
        trace_id=card.get("trace_id"),
        details={
            "theme": card.get("theme"),
            "confidence": card.get("confidence"),
            "needs_clarification": card.get("needs_clarification"),
            "proposed_action": card.get("proposed_action"),
            "citation_count": len(card.get("citations") or []),
            "protected_identifiers": card.get("protected_identifiers"),
        },
    )
    if payload.include_debug:
        card["retrieval_debug"] = answer.get("retrieval_debug")
        card["query_rewrite_debug"] = answer.get("query_rewrite_debug")
        card["generation_mode"] = answer.get("generation_mode")
        card["generation_provider"] = answer.get("generation_provider")
        card["generation_model"] = answer.get("generation_model")
    return card


@app.post("/api/v1/messages/{message_id}/feedback", status_code=201)
async def add_feedback(
    message_id: str,
    payload: FeedbackCreate,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    feedback_id = f"fb_{uuid.uuid4().hex}"
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO copilot_feedback (
                feedback_id, tenant_id, message_id, actor_id, rating, reason_code, comment
            )
            SELECT $1,$2,m.message_id,$3,$4,$5,$6
            FROM support_message m
            WHERE m.message_id = $7 AND m.tenant_id = $2 AND m.role = 'assistant'
            ON CONFLICT (message_id, actor_id) DO UPDATE SET
                rating = EXCLUDED.rating,
                reason_code = EXCLUDED.reason_code,
                comment = EXCLUDED.comment,
                created_at = NOW()
            RETURNING *
            """,
            feedback_id,
            principal.tenant_id,
            principal.user_id,
            payload.rating,
            payload.reason_code,
            payload.comment,
            message_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="assistant_message_not_found")
    return _record(dict(row))


def _risk_level(action: TicketActionCreate) -> str:
    if action.operation in {"grant_service_credit", "refund_payment"}:
        return "financial"
    return "internal_write"


async def _validate_action_evidence(
    *,
    ticket_id: str,
    evidence_ids: list[str],
    principal: Principal,
) -> None:
    if not evidence_ids:
        return
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT evidence_id
            FROM support_message message
            JOIN support_conversation conversation
              ON conversation.conversation_id = message.conversation_id
             AND conversation.tenant_id = message.tenant_id
            CROSS JOIN LATERAL unnest(message.evidence_ids) AS evidence_id
            WHERE conversation.ticket_id = $1
              AND conversation.tenant_id = $2
              AND evidence_id = ANY($3::text[])
            """,
            ticket_id,
            principal.tenant_id,
            evidence_ids,
        )
    verified = {row["evidence_id"] for row in rows}
    missing = sorted(set(evidence_ids) - verified)
    if missing:
        raise HTTPException(status_code=422, detail="evidence_not_linked_to_case")


@app.post("/api/v1/cases/{ticket_id}/actions")
async def execute_case_action(
    ticket_id: str,
    payload: TicketActionCreate,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    require_roles(principal, "support_agent", "support_lead", "support_ops", "billing_ops", "admin")
    await _case_row(ticket_id, principal)
    if payload.operation in {"grant_service_credit", "refund_payment"} and not payload.evidence_ids:
        raise HTTPException(status_code=422, detail="evidence_required_for_financial_action")
    await _validate_action_evidence(
        ticket_id=ticket_id,
        evidence_ids=payload.evidence_ids,
        principal=principal,
    )
    trace_id = current_trace_id() or f"trace_{uuid.uuid4().hex}"
    tool_payload = {
        "ticket_id": ticket_id,
        "operation": payload.operation,
        "reason": payload.reason,
        "actor_id": principal.user_id,
        "actor_role": "support_ops" if principal.role == "support_lead" else principal.role,
        "risk_level": _risk_level(payload),
        "idempotency_key": payload.idempotency_key,
        "trace_id": trace_id,
        "evidence_ids": payload.evidence_ids,
        "prompt_release_id": settings.prompt_release_id,
        "model_version": "configured-rag-model",
    }
    for field in ("new_status", "new_priority", "assignee_id", "amount_cents", "currency"):
        value = getattr(payload, field)
        if value is not None:
            tool_payload[field] = value
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{settings.tool_api_url}/api/v1/tools/ticket_update",
            json=tool_payload,
            headers={
                "X-Service-Token": settings.internal_service_token,
                "X-Actor-ID": principal.user_id,
                "X-Actor-Role": principal.role,
                "X-Tenant-ID": principal.tenant_id,
                "X-Request-ID": request.state.request_id,
            },
        )
    if response.status_code >= 400:
        try:
            dependency_detail = response.json().get("detail")
        except ValueError:
            dependency_detail = None
        raise HTTPException(
            status_code=response.status_code if response.status_code < 500 else 502,
            detail=dependency_detail or "tool_api_rejected_request",
        )
    result = response.json()
    await _audit(
        principal,
        event_type="ticket.action",
        resource_type="ticket",
        resource_id=ticket_id,
        outcome=result.get("status", "unknown"),
        request_id=request.state.request_id,
        trace_id=result.get("trace_id"),
        details={"operation": payload.operation, "approval_id": result.get("approval_id")},
    )
    return result


@app.get("/api/v1/approvals")
async def list_approvals(
    approval_status: str = Query(default="pending", alias="status"),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    require_roles(principal, "support_lead", "support_ops", "billing_ops", "admin", "auditor")
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT approval_id, trace_id, tool_name, action, status, reason_codes,
                   payload - 'description' AS payload, actor_id, reviewer,
                   decision_reason, release_id, created_at, decided_at
            FROM hitl_approval_request
            WHERE tenant_id = $1 AND ($2 = 'all' OR status = $2)
            ORDER BY created_at DESC LIMIT 100
            """,
            principal.tenant_id,
            approval_status,
        )
    return {"items": [_record(dict(row)) for row in rows], "count": len(rows)}


@app.post("/api/v1/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    require_roles(principal, "support_lead", "support_ops", "billing_ops", "admin")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{settings.tool_api_url}/api/v1/approvals/{approval_id}/decision",
            json=payload.model_dump(),
            headers={
                "X-Service-Token": settings.internal_service_token,
                "X-Actor-ID": principal.user_id,
                "X-Actor-Role": principal.role,
                "X-Tenant-ID": principal.tenant_id,
                "X-Request-ID": request.state.request_id,
            },
        )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="approval_not_found")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="approval_decision_failed")
    return response.json()


@app.post("/api/v1/analytics/kpis")
async def query_kpis(
    payload: KpiQuery,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    require_roles(principal, "support_agent", "support_lead", "support_ops", "admin", "auditor")
    tool_payload = payload.model_dump()
    tool_payload["filters"] = dict(tool_payload.get("filters") or {})
    tool_payload["filters"]["data_release_id"] = settings.data_release_id
    tool_payload.update(
        {
            "actor_role": "admin" if principal.role in {"admin", "auditor"} else "support_ops",
            "actor_id": principal.user_id,
            "actor_org_ids": [],
            "trace_id": current_trace_id() or f"trace_{uuid.uuid4().hex}",
            "purpose": "support_ops_analysis",
        }
    )
    if tool_payload["actor_role"] == "support_ops":
        async with acquire() as conn:
            tool_payload["actor_org_ids"] = await conn.fetchval(
                "SELECT COALESCE(array_agg(DISTINCT org_id), ARRAY[]::text[]) FROM ticket_fact WHERE tenant_id = $1",
                principal.tenant_id,
            )
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{settings.tool_api_url}/api/v1/tools/query_support_kpis",
            json=tool_payload,
            headers={
                "X-Actor-ID": principal.user_id,
                "X-Actor-Role": tool_payload["actor_role"],
                "X-Tenant-ID": principal.tenant_id,
                "X-Service-Token": settings.internal_service_token,
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="kpi_tool_unavailable")
    return response.json()


@app.get("/api/v1/operations/overview")
async def operations_overview(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    async with acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE status::text NOT IN ('resolved','closed')) AS open_cases,
              COUNT(*) FILTER (WHERE priority::text = 'p1_critical' AND status::text NOT IN ('resolved','closed')) AS p1_open,
              COUNT(*) FILTER (WHERE sla_due_at < NOW() AND status::text NOT IN ('resolved','closed')) AS sla_breached
            FROM ticket_fact WHERE tenant_id = $1
            """,
            principal.tenant_id,
        )
        quality = await conn.fetchrow(
            """
            SELECT COUNT(*) AS messages,
                   COUNT(*) FILTER (WHERE abstain_reason IS NOT NULL) AS abstained,
                   ROUND(AVG(confidence)::numeric, 3) AS avg_confidence
            FROM support_message WHERE tenant_id = $1 AND role = 'assistant'
            """,
            principal.tenant_id,
        )
        approvals = await conn.fetchval(
            "SELECT COUNT(*) FROM hitl_approval_request WHERE tenant_id = $1 AND status = 'pending'",
            principal.tenant_id,
        )
        data_window = await conn.fetchrow(
            """
            SELECT MIN(created_at::date) AS date_from, MAX(created_at::date) AS date_to
            FROM ticket_fact WHERE tenant_id = $1
            """,
            principal.tenant_id,
        )
        release = await conn.fetchrow(
            """
            SELECT gm.release_id, gm.environment, 'active' AS status,
                   gm.manifest_body->>'data_release_id' AS data_release_id,
                   gm.manifest_body->>'index_release_id' AS index_release_id,
                   gm.manifest_body->>'prompt_release_id' AS prompt_release_id,
                   gm.manifest_body->>'graph_release_id' AS graph_release_id,
                   pointer.updated_at AS promoted_at
            FROM release_environment_pointer pointer
            JOIN governed_release_manifest gm ON gm.release_id = pointer.active_release_id
            WHERE pointer.environment = $1
            ORDER BY pointer.updated_at DESC LIMIT 1
            """,
            settings.release_environment,
        )
    return {
        "case_queue": _record(dict(counts)),
        "copilot_quality": _record(dict(quality)),
        "pending_approvals": approvals,
        "data_window": _record(dict(data_window)),
        "release": _record(dict(release)) if release else None,
        "components": {
            "dagster": "http://localhost:3000",
            "phoenix": "http://localhost:6006",
            "minio": "http://localhost:9001",
            "rag_api": "http://localhost:8000/docs",
            "tool_api": "http://localhost:8001/docs",
        },
    }
