"""Offline evaluator for final-capstone webhook handling-plan golden set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.copilot_api.app.handling_plan import build_handling_plan_card

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SET = ROOT / "evals/sets/final_capstone_webhook_plan_v1.jsonl"


def _synthetic_rag(case: dict) -> dict:
    expect_abstain = bool(case.get("should_abstain"))
    citation_ids = case.get("expected_citation_ids") or []
    citations = []
    for doc_id in citation_ids:
        citations.append(
            {
                "evidence_id": f"ev:{doc_id}",
                "source_id": doc_id,
                "doc_id": doc_id,
                "chunk_id": "chunk-synth",
                "quote": "synthetic grounded snippet for offline eval",
            }
        )
    keywords = case.get("expected_keywords") or []
    answer_lines = [str(case.get("expected_answer") or "Grounded recovery guidance.")]
    for idx, word in enumerate(keywords[:3], start=1):
        answer_lines.append(f"{idx}. Follow documented guidance involving {word}.")
    return {
        "answer": "\n".join(answer_lines),
        "citations": [] if expect_abstain else citations,
        "evidence_ids": [] if expect_abstain else [c["evidence_id"] for c in citations],
        "confidence": 0.0 if expect_abstain else 0.8,
        "abstain_reason": "no_retrieval_results" if expect_abstain else None,
        "release_id": "capstone-final-webhook-v1",
        "trace_id": f"trace-{case['case_id']}",
        "data_release_id": "data-capstone-v1",
        "index_release_id": "index-capstone-v1",
        "prompt_release_id": "prompt-capstone-v1",
    }


def evaluate(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    passed = 0
    for case in rows:
        card = build_handling_plan_card(question=case["query"], rag=_synthetic_rag(case))
        errors: list[str] = []
        for token in case.get("required_identifiers") or []:
            blob = " ".join(
                [
                    card.get("summary") or "",
                    " ".join(card.get("protected_identifiers") or []),
                    " ".join(card.get("steps") or []),
                ]
            )
            if token not in blob and token.upper() not in blob.upper():
                errors.append(f"missing_identifier:{token}")
        expect_abstain = bool(case.get("should_abstain"))
        if expect_abstain and not card.get("abstain_reason") and not card.get("needs_clarification"):
            errors.append("expected_abstain_or_clarification")
        if not expect_abstain and not card.get("citations"):
            errors.append("expected_citations")
        expected_control = case.get("expected_action_control")
        if expected_control and card.get("proposed_action", {}).get("control") != expected_control:
            errors.append(
                f"action_control_expected_{expected_control}_got_{card.get('proposed_action', {}).get('control')}"
            )
        if case["case_id"].startswith("C6") and card.get("proposed_action", {}).get("control") != "hitl":
            errors.append("financial_must_hitl")
        ok = not errors
        passed += int(ok)
        results.append({"case_id": case["case_id"], "passed": ok, "errors": errors, "card": card})
    report = {
        "dataset": str(path),
        "total": len(rows),
        "passed": passed,
        "pass_rate": round(passed / max(len(rows), 1), 4),
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_SET)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/capstone/final_webhook_plan_eval.json")
    args = parser.parse_args()
    report = evaluate(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "pass_rate": report["pass_rate"]}, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
