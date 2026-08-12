"""End-to-end demo: flip a ticket to resolved and show resolution_rate movement."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path

import asyncpg

from app.kpi_query import query_support_kpis

OUT = Path("reports/milestone_resolution_rate/e2e_demo.json")


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        before_rows = await conn.fetch(
            """
            select metric_date::text as metric_date, product_line, metric_value::float as metric_value
            from analytics.agent_tool_input_view
            where metric_name = 'resolution_rate'
            order by metric_date desc, product_line
            limit 5
            """
        )
        target = await conn.fetchrow(
            """
            select ticket_id, status, product_line, cast(created_at as date) as created_date
            from ticket_fact
            where lower(status::text) not in ('resolved', 'closed')
            order by created_at desc
            limit 1
            """
        )
        if target is None:
            raise SystemExit("no unresolved ticket found")

        await conn.execute(
            """
            update ticket_fact
            set status = 'resolved',
                resolved_at = coalesce(resolved_at, now()),
                updated_at = now()
            where ticket_id = $1
            """,
            target["ticket_id"],
        )
        payload = {
            "flipped_ticket_id": target["ticket_id"],
            "from_status": target["status"],
            "to_status": "resolved",
            "product_line": target["product_line"],
            "created_date": target["created_date"].isoformat(),
            "resolution_rate_before_sample": [dict(r) for r in before_rows],
        }
    finally:
        await conn.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
