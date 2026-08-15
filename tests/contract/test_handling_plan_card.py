"""Contract and unit tests for handling plan card (final capstone)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from services.copilot_api.app.handling_plan import (
    build_handling_plan_card,
    decide_action,
    extract_protected_identifiers,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts/product/handling_plan_card.schema.json").read_text(encoding="utf-8"))


def _rag(*, answer: str, evidence: bool = True, confidence: float = 0.82, abstain: str | None = None) -> dict:
    citations = []
    evidence_ids = []
    if evidence:
        citations = [
            {
                "evidence_id": "ev-webhook-401",
                "source_id": "doc:capstone:workspace-webhook-signature-401",
                "doc_id": "doc:capstone:workspace-webhook-signature-401",
                "chunk_id": "chunk-1",
                "quote": "dual-secret mode",
            }
        ]
        evidence_ids = ["ev-webhook-401"]
    return {
        "answer": answer,
        "citations": citations,
        "evidence_ids": evidence_ids,
        "confidence": confidence,
        "abstain_reason": abstain,
        "release_id": "capstone-final-webhook-v1",
        "trace_id": "trace-test-001",
        "data_release_id": "data-capstone-v1",
        "index_release_id": "index-capstone-v1",
        "prompt_release_id": "prompt-capstone-v1",
    }


def test_schema_accepts_valid_plan_card():
    card = build_handling_plan_card(
        question="How do I fix WS-WEBHOOK-401 after rotating the signing secret?",
        rag=_rag(
            answer=(
                "Signature verification failed after secret rotation.\n"
                "1. Enable dual-secret acceptance for 24 hours.\n"
                "2. Replay one failed delivery with the same event id.\n"
                "3. Retire the old secret only after 2xx success."
            )
        ),
    )
    jsonschema.validate(card, SCHEMA)
    assert card["theme"] == "webhook-troubleshooting"
    assert "WS-WEBHOOK-401" in card["protected_identifiers"]
    assert 1 <= len(card["steps"]) <= 3
    assert card["citations"][0]["evidence_id"] == "ev-webhook-401"
    assert card["proposed_action"]["operation"] == "add_internal_note"
    assert card["proposed_action"]["control"] == "confirm"


def test_identifier_preservation_and_financial_hitl():
    ids = extract_protected_identifiers("Customer hit WS-WEBHOOK-401 and HTTP 401 on v3.2")
    assert "WS-WEBHOOK-401" in ids
    action = decide_action("Please grant service credit for the outage", has_evidence=True, abstain=False)
    assert action == {
        "operation": "grant_service_credit",
        "control": "hitl",
        "reason": "financial_side_effect_requires_hitl",
    }


def test_ambiguous_question_clarifies_without_fake_citations():
    card = build_handling_plan_card(
        question="webhook not working",
        rag=_rag(answer="", evidence=False, confidence=0.0, abstain="no_retrieval_results"),
    )
    jsonschema.validate(card, SCHEMA)
    assert card["needs_clarification"] is True
    assert card["abstain_reason"]
    assert card["citations"] == []
    assert card["proposed_action"]["operation"] == "none"


def test_no_evidence_abstains():
    card = build_handling_plan_card(
        question="Explain quantum webhook entanglement for Northstar",
        rag=_rag(answer="", evidence=False, confidence=0.0, abstain="no_retrieval_results"),
    )
    assert card["abstain_reason"] == "no_retrieval_results"
    assert card["steps"] == []
    assert card["proposed_action"]["control"] == "none"
