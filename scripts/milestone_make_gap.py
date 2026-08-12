"""Manufacture a one-day ticket gap for Week03 backfill demo."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg

REPORT = Path("reports/week03/gap_manufacture.json")


async def main() -> None:
    dsn = os.environ.get(
        "DATABASE_URL", "postgresql://omni:omnipass@postgres:5432/omnisupport"
    ).replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            """
            select cast(created_at as date) as d, count(*)::int as c
            from ticket_fact
            group by 1
            order by c desc
            limit 1
            """
        )
        if row is None:
            raise SystemExit("ticket_fact is empty; ingest first")

        gap_date = row["d"].isoformat()
        before = await conn.fetchval("select count(*)::int from ticket_fact")
        deleted_fact = await conn.execute(
            "delete from ticket_fact where cast(created_at as date) = $1::date",
            row["d"],
        )
        deleted_raw = await conn.execute(
            """
            delete from raw_ticket_event
            where cast((raw_payload->>'created_at')::timestamptz as date) = $1::date
            """,
            row["d"],
        )
        after = await conn.fetchval("select count(*)::int from ticket_fact")
        payload = {
            "gap_date": gap_date,
            "deleted_ticket_fact": deleted_fact,
            "deleted_raw_ticket_event": deleted_raw,
            "ticket_fact_before": before,
            "ticket_fact_after": after,
            "note": "Simulated ingest outage for one created_at partition",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
