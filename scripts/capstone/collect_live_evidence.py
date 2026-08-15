"""Live final-capstone evidence collector for webhook handling plan."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assignments/final_capstone/zhuangzhaoxiong/reports/live_evidence.json"
PRODUCT = "http://127.0.0.1:8002" if os.environ.get("LIVE_EVIDENCE_HOST") == "1" else "http://copilot_api:8002"


def main() -> None:
    client = httpx.Client(timeout=60.0)
    login = client.post(
        f"{PRODUCT}/api/v1/auth/login",
        json={"email": "agent@northstar.demo", "password": "Agent@2026"},
    )
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    cases_list = client.get(f"{PRODUCT}/api/v1/cases", headers=headers, params={"limit": 20})
    cases_list.raise_for_status()
    ticket = next(
        item
        for item in cases_list.json()["items"]
        if item.get("product_line") == "northstar_workspace"
    )
    ticket_id = ticket["ticket_id"]

    conv = client.post(
        f"{PRODUCT}/api/v1/cases/{ticket_id}/conversations",
        headers=headers,
        json={"title": "Final capstone webhook plan"},
    )
    conv.raise_for_status()
    conversation_id = conv.json()["conversation_id"]

    # Ground evidence onto the case via normal ask path first.
    grounded = client.post(
        f"{PRODUCT}/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
        json={
            "question": "How do I recover WS-WEBHOOK-401 after signing secret rotation?",
            "retrieval_mode": "hybrid",
            "include_debug": True,
        },
    )
    grounded.raise_for_status()
    grounded_body = grounded.json()
    evidence_ids = grounded_body.get("evidence_ids") or [
        c.get("evidence_id") for c in grounded_body.get("citations") or [] if c.get("evidence_id")
    ]

    plan_specs = [
        (
            "C1",
            "Customer deliveries fail with WS-WEBHOOK-401 after signing secret rotation on Workspace 3.2. What is the recovery order?",
        ),
        ("C3", "webhook not working"),
        (
            "C6",
            "Webhook outage caused missed orders; please grant service credit after we confirm WS-WEBHOOK-5XX.",
        ),
    ]
    plans = {}
    for case_id, question in plan_specs:
        resp = client.post(
            f"{PRODUCT}/api/v1/handling-plan",
            headers=headers,
            json={
                "question": question,
                "ticket_id": ticket_id,
                "product_line": "northstar_workspace",
                "retrieval_mode": "hybrid",
                "include_debug": True,
            },
        )
        plans[case_id] = {"status_code": resp.status_code, "body": resp.json()}

    note_key = f"final-capstone-note-{uuid.uuid4().hex[:10]}"
    note_payload = {
        "operation": "add_internal_note",
        "reason": "Documented WS-WEBHOOK-401 dual-secret recovery steps for the customer.",
        "evidence_ids": evidence_ids[:1],
        "idempotency_key": note_key,
    }
    note1 = client.post(f"{PRODUCT}/api/v1/cases/{ticket_id}/actions", headers=headers, json=note_payload)
    note2 = client.post(f"{PRODUCT}/api/v1/cases/{ticket_id}/actions", headers=headers, json=note_payload)

    credit = client.post(
        f"{PRODUCT}/api/v1/cases/{ticket_id}/actions",
        headers=headers,
        json={
            "operation": "grant_service_credit",
            "reason": "Compensate webhook outage after confirming WS-WEBHOOK-5XX with evidence.",
            "amount_cents": 2500,
            "currency": "USD",
            "evidence_ids": evidence_ids[:2] or evidence_ids[:1],
            "idempotency_key": f"final-capstone-credit-{uuid.uuid4().hex[:10]}",
        },
    )
    credit_body = credit.json()
    approval_id = credit_body.get("approval_id") or (credit_body.get("result") or {}).get("approval_id")

    resume = None
    if approval_id:
        admin = client.post(
            f"{PRODUCT}/api/v1/auth/login",
            json={"email": "admin@northstar.demo", "password": "Admin@2026"},
        )
        admin.raise_for_status()
        admin_headers = {"Authorization": f"Bearer {admin.json()['access_token']}"}
        resume = client.post(
            f"{PRODUCT}/api/v1/approvals/{approval_id}/decision",
            headers=admin_headers,
            json={"approved": True, "reason": "Approved after reviewing webhook outage evidence."},
        )

    payload = {
        "ticket_id": ticket_id,
        "conversation_id": conversation_id,
        "grounded_message": {
            "message_id": grounded_body.get("message_id"),
            "trace_id": grounded_body.get("trace_id"),
            "evidence_ids": evidence_ids,
            "generation_mode": grounded_body.get("generation_mode"),
        },
        "plans": plans,
        "note_first": {"status_code": note1.status_code, "body": note1.json()},
        "note_replay": {"status_code": note2.status_code, "body": note2.json()},
        "credit": {"status_code": credit.status_code, "body": credit_body},
        "credit_resume": None
        if resume is None
        else {"status_code": resume.status_code, "body": resume.json()},
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(OUT),
                "c1_trace": plans["C1"]["body"].get("trace_id"),
                "c1_citations": len(plans["C1"]["body"].get("citations") or []),
                "c1_control": (plans["C1"]["body"].get("proposed_action") or {}).get("control"),
                "c3_clarify_or_abstain": bool(
                    plans["C3"]["body"].get("needs_clarification") or plans["C3"]["body"].get("abstain_reason")
                ),
                "c6_control": (plans["C6"]["body"].get("proposed_action") or {}).get("control"),
                "note_status": [note1.status_code, note2.status_code],
                "note_result_status": [
                    note1.json().get("status"),
                    note2.json().get("status"),
                ],
                "credit_status": credit.status_code,
                "credit_result_status": credit_body.get("status"),
                "resume_status": None if resume is None else resume.status_code,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
