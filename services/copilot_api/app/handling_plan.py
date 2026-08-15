"""Build a structured handling plan card from a grounded RAG response.

This module is intentionally deterministic and policy-first:
- no evidence => clarify/abstain, never invent root cause
- financial side effects => HITL
- low-risk note => explicit confirm
- error codes / versions in the question must be preserved in summary metadata
"""

from __future__ import annotations

import re
from typing import Any

PROTECTED_IDENTIFIER_RE = re.compile(
    r"\b(?:WS-[A-Z0-9-]{3,}|HTTP\s?[0-9]{3}|v?\d+\.\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
ERROR_CODE_RE = re.compile(r"\bWS-[A-Z0-9-]{3,}\b", re.IGNORECASE)
FINANCIAL_RE = re.compile(
    r"\b(credit|refund|compensation|赔偿|补偿|退款|授信)\b",
    re.IGNORECASE,
)
BULK_HIGH_RISK_RE = re.compile(
    r"\b((?:do|perform|run|enable|execute)?\s*bulk\s+replay|force-?reset(?:\s+the\s+secret)?|pause\s+subscription|批量重放|强制重置)\b",
    re.IGNORECASE,
)
AMBIGUOUS_RE = re.compile(
    r"\b(something wrong|not working|坏了|异常|failed|报错)\b",
    re.IGNORECASE,
)


def extract_protected_identifiers(question: str) -> list[str]:
    found = PROTECTED_IDENTIFIER_RE.findall(question or "")
    uniq: list[str] = []
    for item in found:
        if item not in uniq:
            uniq.append(item)
    return uniq


def _steps_from_answer(answer: str, *, limit: int = 3) -> list[str]:
    lines = []
    for raw in (answer or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^(\d+[\).\]]\s+|[-*]\s+)", "", line).strip()
        if len(line) >= 8:
            lines.append(line[:300])
        if len(lines) >= limit:
            break
    if lines:
        return lines[:limit]
    sentences = [part.strip() for part in re.split(r"(?<=[。.!？?])\s*", answer or "") if part.strip()]
    return [s[:300] for s in sentences[:limit]]


def _citations_from_rag(rag: dict[str, Any]) -> list[dict[str, Any]]:
    citations = []
    for item in rag.get("citations") or []:
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            continue
        citations.append(
            {
                "evidence_id": evidence_id,
                "source": item.get("source_id") or item.get("doc_id") or "unknown",
                "doc_id": item.get("doc_id"),
                "chunk_id": item.get("chunk_id"),
                "quote": item.get("quote"),
            }
        )
    return citations


def decide_action(question: str, *, has_evidence: bool, abstain: bool) -> dict[str, Any]:
    if abstain or not has_evidence:
        return {"operation": "none", "control": "none", "reason": "no_safe_action_without_evidence"}
    if FINANCIAL_RE.search(question or ""):
        return {
            "operation": "grant_service_credit",
            "control": "hitl",
            "reason": "financial_side_effect_requires_hitl",
        }
    if BULK_HIGH_RISK_RE.search(question or ""):
        return {
            "operation": "none",
            "control": "none",
            "reason": "high_risk_bulk_or_privileged_change_not_auto_executable",
        }
    return {
        "operation": "add_internal_note",
        "control": "confirm",
        "reason": "low_risk_note_requires_explicit_confirm",
    }


def needs_clarification(question: str, *, has_evidence: bool) -> bool:
    q = (question or "").strip()
    if not q:
        return True
    # Ambiguous / underspecified asks must clarify even if retrieval returns noisy hits.
    if not ERROR_CODE_RE.search(q) and (AMBIGUOUS_RE.search(q) or len(q.split()) < 6):
        return True
    if ERROR_CODE_RE.search(q):
        return False
    if has_evidence:
        return False
    return False


def build_handling_plan_card(
    *,
    question: str,
    rag: dict[str, Any],
    theme: str = "webhook-troubleshooting",
) -> dict[str, Any]:
    citations = _citations_from_rag(rag)
    confidence = float(rag.get("confidence") or 0.0)
    abstain_reason = rag.get("abstain_reason")
    identifiers = extract_protected_identifiers(question)
    clarify = needs_clarification(question, has_evidence=bool(citations))

    if clarify:
        # Do not present noisy retrieval as a concrete diagnosis for vague asks.
        citations = []
        abstain_reason = abstain_reason or "missing_product_or_error_context"
        summary = "需要补充产品线、Webhook 错误码或 HTTP 状态后才能给出可执行方案。"
        steps: list[str] = []
        confidence = min(confidence, 0.2)
    elif not citations:
        abstain_reason = abstain_reason or "no_retrieval_results"
        summary = "当前知识库未检索到可支持的证据，拒绝给出确定诊断。"
        steps = []
        confidence = 0.0
    else:
        answer_text = str(rag.get("answer") or "").strip()
        summary = answer_text.splitlines()[0][:500] if answer_text else "已检索到相关证据，按文档顺序排查。"
        for token in identifiers:
            if token not in summary:
                summary = f"{summary} (preserved: {token})"
                break
        steps = _steps_from_answer(answer_text, limit=3)
        # Low confidence with real citations stays cautious but still returns grounded steps.
        # Only mark abstain when retrieval itself failed.
        if confidence < 0.15 and not identifiers:
            abstain_reason = abstain_reason or "low_confidence"
        else:
            abstain_reason = None

    # Proposed actions are recommendations under explicit confirm/HITL, not auto-executions.
    action = decide_action(
        question,
        has_evidence=bool(citations),
        abstain=abstain_reason is not None and not citations,
    )

    return {
        "summary": summary,
        "steps": steps,
        "citations": citations,
        "confidence": confidence,
        "needs_clarification": clarify and abstain_reason is not None,
        "abstain_reason": abstain_reason,
        "proposed_action": action,
        "release_id": rag.get("release_id") or "unknown",
        "trace_id": rag.get("trace_id") or "unknown",
        "data_release_id": rag.get("data_release_id"),
        "index_release_id": rag.get("index_release_id"),
        "prompt_release_id": rag.get("prompt_release_id"),
        "theme": theme,
        "protected_identifiers": identifiers,
    }
