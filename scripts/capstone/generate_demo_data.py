"""Generate a deterministic, privacy-safe Northstar support dataset.

The records are realistic enough to exercise ingest, dbt, queue prioritization,
SLA metrics, controlled actions, and replay. They are fictional and contain no
customer production data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_AS_OF = datetime(2026, 7, 21, tzinfo=timezone.utc)

PRODUCTS = {
    "northstar_workspace": {
        "versions": ["4.2", "4.1", "3.2"],
        "errors": ["WS-AUTH-001", "WS-PERM-002", "WS-WEBHOOK-005", "WS-API-404"],
        "subjects": [
            "SSO login loop after certificate rotation",
            "Updated role is not visible after permission change",
            "Webhook delivery returns HTTP 404",
            "Workspace sync remains stale after reconnect",
        ],
    },
    "northstar_edge_gateway": {
        "versions": ["3.9", "3.8", "3.7"],
        "errors": ["EG-CONN-001", "EG-CERT-005", "EG-BOOT-004", "EG-SENSOR-003"],
        "subjects": [
            "Gateway reports certificate verify failed",
            "Device remains offline after network change",
            "Gateway boot loop after trust-store update",
            "Telemetry missing from one deployment site",
        ],
    },
    "northstar_studio": {
        "versions": ["1.5", "1.4", "1.3"],
        "errors": ["ST-SCHED-005", "ST-EXEC-002", "ST-MONITOR-003", "ST-INTEG-004"],
        "subjects": [
            "Scheduled workflow executed twice",
            "Backfill stopped after partial completion",
            "Workflow run remains pending",
            "Integration output row count is lower than expected",
        ],
    },
}
ORGANIZATIONS = [
    ("org-atlas-retail", "Atlas Retail", "enterprise"),
    ("org-cobalt-health", "Cobalt Health", "enterprise"),
    ("org-meridian-logistics", "Meridian Logistics", "professional"),
    ("org-nova-energy", "Nova Energy", "professional"),
    ("org-prism-data", "Prism Data", "standard"),
    ("org-vertex-cloud", "Vertex Cloud", "standard"),
    ("org-summit-media", "Summit Media", "professional"),
    ("org-horizon-labs", "Horizon Labs", "free"),
]
CATEGORY_BY_SUBJECT = {
    "SSO": "authentication",
    "role": "authentication",
    "Webhook": "connectivity",
    "sync": "performance",
    "certificate": "security",
    "offline": "connectivity",
    "boot": "bug_report",
    "Telemetry": "connectivity",
    "Scheduled": "bug_report",
    "Backfill": "configuration",
    "pending": "performance",
    "row count": "performance",
}


def stable_id(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def generate_tickets(
    *,
    count: int,
    output: Path,
    seed: int,
    as_of: datetime,
    data_release_id: str | None = None,
) -> list[dict]:
    data_release_id = data_release_id or os.environ.get(
        "CAPSTONE_DATA_RELEASE_ID", "data-capstone-v1"
    )
    rng = random.Random(seed)
    products = list(PRODUCTS)
    records: list[dict] = []
    for index in range(1, count + 1):
        product = products[index % len(products)]
        product_config = PRODUCTS[product]
        subject = product_config["subjects"][index % len(product_config["subjects"])]
        category = next(
            (value for key, value in CATEGORY_BY_SUBJECT.items() if key in subject), "other"
        )
        org_id, org_name, sla_tier = ORGANIZATIONS[index % len(ORGANIZATIONS)]
        created_at = as_of - timedelta(
            days=rng.randint(0, 89), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        priority = rng.choices(
            ["p1_critical", "p2_high", "p3_medium", "p4_low"],
            weights=[5, 18, 52, 25],
            k=1,
        )[0]
        status = rng.choices(
            ["open", "pending", "in_progress", "resolved", "closed", "escalated"],
            weights=[18, 13, 24, 25, 14, 6],
            k=1,
        )[0]
        sla_hours = {"enterprise": 4, "professional": 8, "standard": 24, "free": 72}[sla_tier]
        resolved_at = None
        if status in {"resolved", "closed"}:
            resolved_at = created_at + timedelta(hours=rng.randint(1, sla_hours * 2))
        error_code = product_config["errors"][index % len(product_config["errors"])]
        # Keep the product data namespace disjoint from Week01-Week14 teaching fixtures.
        ticket_id = f"TKT-{created_at.strftime('%Y%m%d')}-{900000 + index:06d}"
        records.append(
            {
                "ticket_id": ticket_id,
                "schema_version": "ticket_v1",
                "source_id": "structured:tickets:capstone-v1",
                "ingest_batch_id": "batch-capstone-v1",
                "tenant_id": "northstar-demo",
                "data_release_id": data_release_id,
                "customer_id": f"cust-cap-{stable_id(org_id + ':' + str(index % 12))}",
                "org_id": org_id,
                "status": status,
                "priority": priority,
                "category": category,
                "product_line": product,
                "product_version": product_config["versions"][index % len(product_config["versions"])],
                "subject": subject,
                "description": (
                    f"{org_name} reports {subject.lower()}. The observed code is {error_code}. "
                    "Customer identifiers were replaced by the course data factory before ingestion."
                ),
                "error_codes": [error_code],
                "asset_ids": [f"asset-{stable_id(ticket_id, 8)}"],
                "assignee_id": None if index % 4 == 0 else f"agent-{(index % 7) + 1:02d}",
                "sla_tier": sla_tier,
                "sla_due_at": (created_at + timedelta(hours=sla_hours)).isoformat(),
                "created_at": created_at.isoformat(),
                "updated_at": (resolved_at or created_at + timedelta(hours=rng.randint(0, 36))).isoformat(),
                "resolved_at": resolved_at.isoformat() if resolved_at else None,
                "pii_level": "low",
                "pii_redacted": True,
                "quality_gate": "pass",
                "owner": "course-data-factory",
                "tags": [product, category, priority, "capstone"],
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return records


def generate_manifests(*, root: Path, output_dir: Path) -> list[Path]:
    grouped = {
        "northstar_workspace": [
            "workspace-admin-recovery.html",
            "workspace-api-webhook.html",
            "workspace-webhook-signature-401.html",
            "workspace-webhook-retry-dedup.html",
        ],
        "northstar_edge_gateway": ["edge-gateway-tls-recovery.html"],
        "northstar_studio": ["studio-job-recovery.html"],
        "cross_product": ["support-credit-policy.html", "security-support-boundary.html"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for product_line, names in grouped.items():
        assets = []
        for name in names:
            path = root / "data" / "capstone" / "knowledge" / name
            raw = path.read_bytes()
            assets.append(
                {
                    "source_id": f"doc:capstone:{path.stem}",
                    "source_url_or_path": str(path),
                    "asset_type": "html",
                    "contract_ref": "omni://contracts/data/doc_asset/v1",
                    "size_bytes": len(raw),
                    "checksum_sha256": hashlib.sha256(raw).hexdigest(),
                    "metadata_status": "complete",
                    "pii_scan_status": "clear",
                    "language": "en",
                    "notes": "Course-authored enterprise support knowledge asset.",
                }
            )
        manifest = {
            "manifest_id": f"manifest-capstone-{product_line.replace('_', '-')}-20260721-001",
            "schema_version": "source_manifest_v1",
            "batch_id": "batch-capstone-knowledge-v1",
            "modality": "document",
            "source_type": "help_center",
            "product_line": product_line,
            "license_tag": "course_synthetic",
            "contract_ref": "omni://contracts/data/doc_asset/v1",
            "load_mode": "full_snapshot",
            "canonization_status": "canonized",
            "gate_policy": {
                "on_missing_checksum": "reject",
                "on_partial_metadata": "warn",
                "on_missing_metadata": "quarantine",
                "on_pii_gap": "quarantine",
                "on_contract_mismatch": "reject",
                "on_unknown_license": "reject",
            },
            "assets": assets,
            "ingest_config": {"parser": "auto", "chunk_size": 420, "chunk_overlap": 60, "pii_scan": True},
            "created_at": "2026-07-21T00:00:00Z",
            "owner": "course-team",
            "notes": "Week15 enterprise capstone knowledge pack.",
        }
        output = output_dir / f"manifest_{product_line}.json"
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        outputs.append(output)
    return outputs


def resolve_as_of(value: str | datetime | None = None) -> datetime:
    raw_value = value or os.environ.get("CAPSTONE_AS_OF")
    if raw_value is None:
        return DEFAULT_AS_OF
    if isinstance(raw_value, datetime):
        parsed = raw_value
    else:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def generate(
    root: Path,
    *,
    count: int = 240,
    seed: int = 20260721,
    as_of: str | datetime | None = None,
) -> dict:
    generated = root / "data" / "generated" / "capstone"
    as_of_value = resolve_as_of(as_of)
    tickets_path = generated / "tickets.jsonl"
    tickets = generate_tickets(count=count, output=tickets_path, seed=seed, as_of=as_of_value)
    manifests = generate_manifests(root=root, output_dir=generated / "manifests")
    summary = {
        "tickets_path": str(tickets_path),
        "ticket_count": len(tickets),
        "manifest_paths": [str(path) for path in manifests],
        "generated_at": as_of_value.isoformat(),
        "seed": seed,
    }
    (generated / "generation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--count", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--as-of", default=None, help="ISO-8601 data snapshot time")
    args = parser.parse_args()
    print(
        json.dumps(
            generate(args.root.resolve(), count=args.count, seed=args.seed, as_of=args.as_of),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
