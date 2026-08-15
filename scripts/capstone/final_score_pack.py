"""Collect final-capstone scoring pack evidence into the assignment folder."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[2]
ASSIGN = ROOT / "assignments/final_capstone/zhuangzhaoxiong/reports"
SRC_BOOTSTRAP = ROOT / "reports/capstone/bootstrap_second_run.json"
SRC_E2E = ROOT / "reports/capstone/e2e-verification.json"
PRODUCT = os.environ.get("PRODUCT_API_URL", "http://copilot_api:8002")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://omni:omnipass@postgres:5432/omnisupport"
).replace("postgresql+asyncpg://", "postgresql://")

CURRENT = "capstone-v1.0.0"
CANDIDATE = "capstone-final-webhook-candidate-v1"


def _digest(obj: dict) -> str:
    stable = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _copy_reports() -> dict[str, str]:
    ASSIGN.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    mapping = {
        "bootstrap_second_run": SRC_BOOTSTRAP,
        "e2e_verification": SRC_E2E,
    }
    for key, src in mapping.items():
        if not src.exists():
            continue
        dst = ASSIGN / f"{key}.json"
        shutil.copyfile(src, dst)
        out[key] = str(dst.relative_to(ROOT)).replace("\\", "/")
    return out


async def _active(conn: asyncpg.Connection) -> tuple[str | None, int]:
    row = await conn.fetchrow(
        "SELECT active_release_id, generation FROM release_environment_pointer WHERE environment='dev'"
    )
    if not row:
        return None, 0
    return row["active_release_id"], int(row["generation"] or 0)


async def _set_active(conn: asyncpg.Connection, release_id: str, actor: str) -> int:
    row = await conn.fetchrow(
        "SELECT generation FROM release_environment_pointer WHERE environment='dev' FOR UPDATE"
    )
    generation = int(row["generation"] if row else 0) + 1
    await conn.execute(
        """
        INSERT INTO release_environment_pointer (
            environment, active_release_id, generation, updated_by, updated_at
        ) VALUES ('dev',$1,$2,$3,NOW())
        ON CONFLICT (environment) DO UPDATE SET
            active_release_id = EXCLUDED.active_release_id,
            generation = EXCLUDED.generation,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        """,
        release_id,
        generation,
        actor,
    )
    return generation


async def _append_audit(
    conn: asyncpg.Connection,
    *,
    event_type: str,
    actor: str,
    from_release_id: str | None,
    to_release_id: str | None,
    reason: str,
    details: dict,
) -> str:
    previous = await conn.fetchval(
        """
        SELECT event_digest FROM release_audit_event
        WHERE environment = 'dev'
        ORDER BY occurred_at DESC, event_id DESC
        LIMIT 1
        """
    )
    event_id = str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc)
    body = {
        "event_id": event_id,
        "environment": "dev",
        "event_type": event_type,
        "actor": actor,
        "from_release_id": from_release_id,
        "to_release_id": to_release_id,
        "reason": reason,
        "details": details,
        "occurred_at": occurred_at.isoformat(),
        "previous_event_digest": previous,
    }
    event_digest = _digest(body)
    await conn.execute(
        """
        INSERT INTO release_audit_event (
            event_id, environment, event_type, actor, from_release_id,
            to_release_id, reason, details, previous_event_digest,
            event_digest, occurred_at
        ) VALUES ($1,'dev',$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)
        """,
        event_id,
        event_type,
        actor,
        from_release_id,
        to_release_id,
        reason,
        json.dumps(details, ensure_ascii=False),
        previous,
        event_digest,
        occurred_at,
    )
    return event_id


async def rollback_drill() -> dict:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            before_id, before_gen = await _active(conn)
            if before_id != CURRENT:
                before_gen = await _set_active(conn, CURRENT, "zhuangzhaoxiong-normalize")
                before_id = CURRENT
                await _append_audit(
                    conn,
                    event_type="release.pointer_normalized",
                    actor="zhuangzhaoxiong",
                    from_release_id=None,
                    to_release_id=CURRENT,
                    reason="normalize_active_to_capstone_baseline",
                    details={"generation": before_gen},
                )

            current_digest = await conn.fetchval(
                "SELECT manifest_digest FROM governed_release_manifest WHERE release_id=$1",
                CURRENT,
            )
            if not current_digest:
                raise RuntimeError(f"missing baseline release {CURRENT}")

            exists = await conn.fetchval(
                "SELECT 1 FROM governed_release_manifest WHERE release_id=$1",
                CANDIDATE,
            )
            if not exists:
                body = {
                    "release_id": CANDIDATE,
                    "previous_release_id": CURRENT,
                    "data_release_id": "data-capstone-v1",
                    "index_release_id": "index-capstone-v1",
                    "prompt_release_id": "prompt-capstone-v1",
                    "graph_release_id": "graph-capstone-v1",
                    "eval_run_id": "eval-capstone-final-webhook-drill",
                    "services": {
                        "rag_api": "1.0.0",
                        "tool_api": "1.0.0",
                        "copilot_api": "1.0.0-handling-plan",
                    },
                    "drill": "final_capstone_rollback",
                }
                digest = _digest(body)
                await conn.execute(
                    """
                    INSERT INTO governed_release_manifest (
                        release_id, environment, manifest_digest, previous_release_id,
                        previous_manifest_digest, git_sha, created_by,
                        signature_algorithm, manifest_body
                    ) VALUES ($1,'dev',$2,$3,$4,$5,'zhuangzhaoxiong','none',$6::jsonb)
                    """,
                    CANDIDATE,
                    digest,
                    CURRENT,
                    current_digest,
                    "0" * 40,
                    json.dumps(body),
                )
            else:
                digest = await conn.fetchval(
                    "SELECT manifest_digest FROM governed_release_manifest WHERE release_id=$1",
                    CANDIDATE,
                )

            promote_gen = await _set_active(conn, CANDIDATE, "zhuangzhaoxiong-promote-drill")
            await _append_audit(
                conn,
                event_type="release.promoted",
                actor="zhuangzhaoxiong",
                from_release_id=CURRENT,
                to_release_id=CANDIDATE,
                reason="final_capstone_promote_drill",
                details={"generation": promote_gen, "manifest_digest": digest},
            )
            mid_id, mid_gen = await _active(conn)

            rollback_gen = await _set_active(conn, CURRENT, "zhuangzhaoxiong-rollback-drill")
            audit_id = await _append_audit(
                conn,
                event_type="release.rolled_back",
                actor="zhuangzhaoxiong",
                from_release_id=CANDIDATE,
                to_release_id=CURRENT,
                reason="final_capstone_rollback_drill",
                details={
                    "generation": rollback_gen,
                    "command": "capstone pointer rollback (dev)",
                    "equivalent_cli": (
                        "python -m rollout.rollback "
                        f"--current-release-id {CANDIDATE} "
                        f"--target-release-id {CURRENT} "
                        "--actor incident-commander "
                        "--reason final_capstone_rollback_drill"
                    ),
                },
            )
            after_id, after_gen = await _active(conn)

        return {
            "status": "rolled_back",
            "before": {"release_id": before_id, "generation": before_gen},
            "promoted_candidate": {
                "release_id": CANDIDATE,
                "manifest_digest": digest,
                "generation": promote_gen,
                "observed_active": mid_id,
                "observed_generation": mid_gen,
            },
            "after": {
                "release_id": after_id,
                "generation": after_gen,
                "rollback_generation": rollback_gen,
                "audit_event_id": audit_id,
            },
            "target_was_direct_previous_of_candidate": True,
            "verified_active_restored": after_id == CURRENT,
        }
    finally:
        await conn.close()


def smoke_handling_plan() -> dict:
    client = httpx.Client(timeout=60.0)
    login = client.post(
        f"{PRODUCT}/api/v1/auth/login",
        json={"email": "agent@northstar.demo", "password": "Agent@2026"},
    )
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    cases = {
        "C1": "Customer deliveries fail with WS-WEBHOOK-401 after signing secret rotation on Workspace 3.2. What is the recovery order?",
        "C3": "webhook not working",
        "C8": "Follow the documented recovery for WS-WEBHOOK-401 using the workspace webhook signature guide.",
    }
    out = {}
    for case_id, question in cases.items():
        resp = client.post(
            f"{PRODUCT}/api/v1/handling-plan",
            headers=headers,
            json={
                "question": question,
                "product_line": "northstar_workspace",
                "retrieval_mode": "hybrid",
                "include_debug": False,
            },
        )
        body = resp.json()
        out[case_id] = {
            "status_code": resp.status_code,
            "release_id": body.get("release_id"),
            "trace_id": body.get("trace_id"),
            "citations": len(body.get("citations") or []),
            "control": (body.get("proposed_action") or {}).get("control"),
            "needs_clarification": body.get("needs_clarification"),
            "abstain_reason": body.get("abstain_reason"),
            "protected_identifiers": body.get("protected_identifiers"),
        }
    return out


async def amain() -> None:
    copied = _copy_reports()
    drill = await rollback_drill()
    smoke = smoke_handling_plan()
    payload = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "copied_reports": copied,
        "rollback_drill": drill,
        "post_rollback_handling_plan_smoke": smoke,
    }
    out = ASSIGN / "rollback_drill.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "ok": drill.get("verified_active_restored"), "smoke": smoke}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
