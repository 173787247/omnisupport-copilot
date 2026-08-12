"""Demo allow/deny queries for resolution_rate."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg

from app.kpi_query import query_support_kpis

OUT_DIR = Path("reports/week05")


async def _sample_org_id() -> str:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        value = await conn.fetchval(
            """
            select org_id
            from analytics.agent_tool_input_view
            where metric_name = 'resolution_rate' and org_id is not null
            limit 1
            """
        )
        return str(value or "unknown")
    finally:
        await conn.close()


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    org_id = await _sample_org_id()

    allowed = await query_support_kpis(
        {
            "actor_role": "support_ops",
            "actor_id": "ops-demo",
            "actor_org_ids": [org_id],
            "trace_id": "trace-resolution-rate-ok",
            "purpose": "support_ops_analysis",
            "metrics": ["resolution_rate"],
            "date_from": "2026-04-01",
            "date_to": "2026-04-30",
            "dimensions": ["product_line"],
            "limit": 20,
        }
    )
    (OUT_DIR / "query_resolution_rate_allowed.json").write_text(
        json.dumps(allowed, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        "ALLOWED:",
        allowed.get("status"),
        "denial=",
        allowed.get("denial_code"),
        "rows=",
        len(allowed.get("rows", [])),
        "audit=",
        allowed.get("audit_id"),
        "org=",
        org_id,
    )

    denied = await query_support_kpis(
        {
            "actor_role": "viewer",
            "actor_id": "unauthorized",
            "metrics": ["resolution_rate"],
            "date_from": "2026-04-01",
            "date_to": "2026-04-30",
        }
    )
    (OUT_DIR / "query_resolution_rate_denied.json").write_text(
        json.dumps(denied, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        "DENIED:",
        denied.get("status"),
        "denial_code=",
        denied.get("denial_code"),
        "audit=",
        denied.get("audit_id"),
    )

    sample = await query_support_kpis(
        {
            "actor_role": "instructor",
            "actor_id": "instructor-demo",
            "trace_id": "trace-resolution-rate-sample",
            "purpose": "classroom_demo",
            "metrics": ["resolution_rate"],
            "date_from": "2026-04-01",
            "date_to": "2026-04-30",
            "dimensions": ["product_line"],
            "limit": 10,
        }
    )
    (OUT_DIR / "query_resolution_rate_sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print("SAMPLE:", sample.get("status"), "rows=", len(sample.get("rows", [])))


if __name__ == "__main__":
    asyncio.run(main())
