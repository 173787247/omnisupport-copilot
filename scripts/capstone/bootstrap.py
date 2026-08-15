"""Idempotent enterprise capstone bootstrap using the course's real pipelines."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import asyncpg

from pipelines.graph.build import build_graph
from pipelines.graph.models import SourceChunk
from pipelines.graph.store import persist_graph_build
from pipelines.indexing.embedder import build_index
from pipelines.ingestion.doc_ingest import run_doc_ingest
from pipelines.ingestion.ticket_ingest import run_ingest
from pipelines.parse_normalize.run_parse import run_parse_pipeline
from scripts.capstone.generate_demo_data import ORGANIZATIONS, generate

DATA_RELEASE_ID = os.environ.get("CAPSTONE_DATA_RELEASE_ID", "data-capstone-v1")
STALE_DATA_RELEASE_ID = os.environ.get(
    "CAPSTONE_STALE_DATA_RELEASE_ID", f"{DATA_RELEASE_ID}-stale"
)
INDEX_RELEASE_ID = os.environ.get("CAPSTONE_INDEX_RELEASE_ID", "index-capstone-v1")
PROMPT_RELEASE_ID = os.environ.get("CAPSTONE_PROMPT_RELEASE_ID", "prompt-capstone-v1")
GRAPH_RELEASE_ID = os.environ.get("CAPSTONE_GRAPH_RELEASE_ID", "graph-capstone-v1")
RELEASE_ID = os.environ.get("CAPSTONE_RELEASE_ID", "capstone-v1.0.0")


def dsn() -> str:
    return (
        os.environ.get("DATABASE_URL", "postgresql://omni:omnipass@postgres:5432/omnisupport")
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


async def apply_additive_migrations(root: Path) -> None:
    conn = await asyncpg.connect(dsn())
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_schema_migration (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        for migration in sorted((root / "infra" / "migrations").glob("*.sql")):
            if migration.name == "001_init.sql":
                continue
            raw = migration.read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            source = raw.decode("utf-8")
            stored = await conn.fetchval(
                "SELECT checksum FROM app_schema_migration WHERE version = $1",
                migration.name,
            )
            if stored and stored != checksum:
                raise RuntimeError(f"migration checksum mismatch: {migration.name}")
            if stored:
                continue
            async with conn.transaction():
                await conn.execute(source)
                await conn.execute(
                    "INSERT INTO app_schema_migration(version, checksum) VALUES ($1, $2)",
                    migration.name,
                    checksum,
                )
    finally:
        await conn.close()


async def ingest_stage(root: Path, generation: dict[str, Any]) -> dict[str, Any]:
    ticket_report = root / "reports" / "capstone" / "ticket-ingest.json"
    ticket_stats = await run_ingest(
        Path(generation["tickets_path"]),
        "batch-capstone-v1",
        dry_run=False,
        report_path=ticket_report,
        state_path=root / "data" / "generated" / "capstone" / "checkpoint.json",
    )
    document_stats = []
    for manifest_value in generation["manifest_paths"]:
        manifest = Path(manifest_value)
        document_stats.append(
            await run_doc_ingest(
                manifest,
                source_dir=None,
                batch_id="batch-capstone-knowledge-v1",
                dry_run=False,
                report_path=root / "reports" / "capstone" / f"doc-{manifest.stem}.json",
            )
        )
    multimodal_manifest = root / "data" / "seed_manifests" / "manifest_week07_multimodal_v1.json"
    document_stats.append(
        await run_doc_ingest(
            multimodal_manifest,
            source_dir=root / "data" / "week07_media",
            batch_id="batch-capstone-multimodal-v1",
            dry_run=False,
            report_path=root / "reports" / "capstone" / "doc-multimodal.json",
        )
    )
    reconciliation = await enrich_operational_records(root, Path(generation["tickets_path"]))
    return {
        "tickets": ticket_stats,
        "documents": document_stats,
        "snapshot_reconciliation": reconciliation,
    }


async def enrich_operational_records(root: Path, tickets_path: Path) -> dict[str, int]:
    records = [json.loads(line) for line in tickets_path.read_text(encoding="utf-8").splitlines() if line]
    org_names = {org_id: org_name for org_id, org_name, _ in ORGANIZATIONS}
    conn = await asyncpg.connect(dsn())
    try:
        async with conn.transaction():
            ticket_ids = [record["ticket_id"] for record in records]
            customer_ids = sorted({record["customer_id"] for record in records})
            await conn.execute(
                "UPDATE customer_dim SET tenant_id = 'northstar-demo' WHERE customer_id = ANY($1::text[])",
                customer_ids,
            )
            await conn.execute(
                """
                UPDATE ticket_fact
                SET tenant_id = 'northstar-demo', data_release_id = $2
                WHERE ticket_id = ANY($1::text[])
                """,
                ticket_ids,
                DATA_RELEASE_ID,
            )
            retired = await conn.execute(
                """
                UPDATE ticket_fact
                SET tenant_id = 'course-legacy',
                    data_release_id = 'data-capstone-dev-stale'
                WHERE tenant_id = 'northstar-demo'
                  AND data_release_id = $2
                  AND NOT (ticket_id = ANY($1::text[]))
                """,
                ticket_ids,
                DATA_RELEASE_ID,
            )
            for org_id, org_name in org_names.items():
                await conn.execute(
                    "UPDATE customer_dim SET org_name = $1 WHERE org_id = $2",
                    org_name,
                    org_id,
                )
            for record in records:
                comment_id = f"comment_{hashlib.sha256((record['ticket_id'] + ':initial').encode()).hexdigest()[:24]}"
                await conn.execute(
                    """
                    INSERT INTO ticket_comment_fact (
                        comment_id, ticket_id, author_id, author_role, body, created_at
                    ) VALUES ($1,$2,$3,'customer',$4,$5::text::timestamptz)
                    ON CONFLICT (comment_id) DO UPDATE SET
                        ticket_id = EXCLUDED.ticket_id,
                        author_id = EXCLUDED.author_id,
                        author_role = EXCLUDED.author_role,
                        body = EXCLUDED.body,
                        created_at = EXCLUDED.created_at
                    """,
                    comment_id,
                    record["ticket_id"],
                    record["customer_id"],
                    record["description"],
                    record["created_at"],
                )
            current_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM ticket_fact
                WHERE tenant_id = 'northstar-demo' AND data_release_id = $1
                """,
                DATA_RELEASE_ID,
            )
    finally:
        await conn.close()
    return {
        "current_ticket_count": int(current_count),
        "retired_stale_count": int(retired.rsplit(" ", 1)[-1]),
    }


def knowledge_stage(root: Path, generation: dict[str, Any]) -> dict[str, Any]:
    parse_summaries = []
    manifests = [Path(value) for value in generation["manifest_paths"]]
    manifests.append(root / "data" / "seed_manifests" / "manifest_week07_multimodal_v1.json")
    for manifest in manifests:
        name = manifest.stem
        parse_run, gate = run_parse_pipeline(
            manifest_path=manifest,
            parser="auto",
            chunk_strategy_version="section_aware_v1",
            data_release_id=DATA_RELEASE_ID,
            dry_run=False,
            artifacts_dir=root / "artifacts" / "capstone" / name,
            report_json=root / "reports" / "capstone" / f"parse-{name}.json",
            quality_report_md=root / "reports" / "capstone" / f"quality-{name}.md",
            week8_gate_json=root / "reports" / "capstone" / f"gate-{name}.json",
        )
        parse_summaries.append(
            {
                "manifest": str(manifest),
                "status": parse_run.status,
                "chunks": parse_run.chunk_count,
                "week8_ready": gate.week8_ready,
            }
        )
    active_source_ids = sorted(
        {
            asset["source_id"]
            for manifest in manifests
            for asset in json.loads(manifest.read_text(encoding="utf-8"))["assets"]
        }
    )
    reconciliation = asyncio.run(reconcile_knowledge_snapshot(active_source_ids))
    index_stats = asyncio.run(
        build_index(
            index_release_id=INDEX_RELEASE_ID,
            batch_size=32,
            dry_run=False,
            data_release_id=DATA_RELEASE_ID,
            chunk_strategy_version="section_aware_v1",
            report_dir=root / "reports" / "capstone" / "index",
        )
    )
    return {
        "parse": parse_summaries,
        "snapshot_reconciliation": reconciliation,
        "index": index_stats.__dict__,
    }


async def reconcile_knowledge_snapshot(active_source_ids: list[str]) -> dict[str, int]:
    """Retire chunks that no longer belong to the current Capstone source snapshot."""
    conn = await asyncpg.connect(dsn())
    try:
        async with conn.transaction():
            stale_predicate = """
                ks.data_release_id = $1
                AND (
                    NOT (ks.source_id = ANY($2::text[]))
                    OR ks.source_fingerprint IS DISTINCT FROM (
                        SELECT kd.source_fingerprint
                        FROM knowledge_doc kd
                        WHERE kd.doc_id = ks.doc_id
                    )
                )
            """
            anchors = await conn.execute(
                f"""
                UPDATE evidence_anchor ea
                SET data_release_id = $3
                WHERE ea.chunk_id IN (
                    SELECT ks.section_id
                    FROM knowledge_section ks
                    WHERE {stale_predicate}
                )
                """,
                DATA_RELEASE_ID,
                active_source_ids,
                STALE_DATA_RELEASE_ID,
            )
            chunks = await conn.execute(
                f"""
                UPDATE knowledge_section ks
                SET data_release_id = $3,
                    index_release_id = NULL,
                    indexed_at = NULL,
                    allowed_for_indexing = FALSE
                WHERE {stale_predicate}
                """,
                DATA_RELEASE_ID,
                active_source_ids,
                STALE_DATA_RELEASE_ID,
            )
            documents = await conn.execute(
                """
                UPDATE knowledge_doc
                SET data_release_id = $3,
                    index_release_id = NULL,
                    indexed_at = NULL,
                    status = 'retired'
                WHERE data_release_id = $1
                  AND NOT (source_id = ANY($2::text[]))
                """,
                DATA_RELEASE_ID,
                active_source_ids,
                STALE_DATA_RELEASE_ID,
            )
            await conn.execute(
                """
                UPDATE knowledge_doc
                SET status = 'active'
                WHERE data_release_id = $1
                  AND source_id = ANY($2::text[])
                """,
                DATA_RELEASE_ID,
                active_source_ids,
            )
            current_chunks = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM knowledge_section
                WHERE data_release_id = $1
                  AND chunk_strategy_version = 'section_aware_v1'
                  AND allowed_for_indexing = TRUE
                """,
                DATA_RELEASE_ID,
            )
    finally:
        await conn.close()

    def affected(command_tag: str) -> int:
        return int(command_tag.rsplit(" ", 1)[-1])

    return {
        "active_source_count": len(active_source_ids),
        "current_chunk_count": int(current_chunks),
        "retired_document_count": affected(documents),
        "retired_chunk_count": affected(chunks),
        "retired_anchor_count": affected(anchors),
    }


def analytics_stage(root: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env["WEEK05_DATA_RELEASE_ID"] = DATA_RELEASE_ID
    command = [
        "dbt",
        "build",
        "--project-dir",
        str(root / "analytics"),
        "--profiles-dir",
        str(root / "analytics"),
        "--target",
        env.get("DBT_TARGET", "dev"),
    ]
    result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"dbt build failed:\n{result.stdout}\n{result.stderr}")
    return {"status": "pass", "command": " ".join(command)}


def load_capstone_graph_chunks(root: Path) -> list[SourceChunk]:
    """Project the active knowledge snapshot into reviewed GraphRAG source chunks."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    annotation_path = root / "data" / "capstone" / "graph_annotations_v1.json"
    annotation_document = json.loads(annotation_path.read_text(encoding="utf-8"))
    source_annotations = annotation_document["sources"]
    query = """
        SELECT
            ks.section_id AS chunk_id,
            COALESCE(ks.evidence_anchor_ids[1], ea.anchor_id) AS evidence_id,
            ks.doc_id,
            ks.source_id,
            ks.content,
            ks.section_path,
            ks.page_no,
            ks.bbox,
            kd.title,
            kd.source_url,
            kd.doc_version,
            kd.product_line::text AS stored_product_line,
            kd.visibility_scope
        FROM knowledge_section ks
        JOIN knowledge_doc kd ON kd.doc_id = ks.doc_id
        LEFT JOIN LATERAL (
            SELECT anchor_id
            FROM evidence_anchor
            WHERE chunk_id = ks.section_id
            ORDER BY anchor_id
            LIMIT 1
        ) ea ON TRUE
        WHERE ks.data_release_id = %s
          AND ks.allowed_for_indexing = TRUE
        ORDER BY ks.source_id, ks.chunk_index, ks.section_id
    """
    with psycopg2.connect(dsn()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, (DATA_RELEASE_ID,))
        rows = cursor.fetchall()

    chunks = []
    missing_sources = set()
    for row in rows:
        source_id = row["source_id"]
        annotation = source_annotations.get(source_id)
        if annotation is None:
            missing_sources.add(source_id)
            continue
        if not row["evidence_id"]:
            raise RuntimeError(f"knowledge chunk {row['chunk_id']} has no evidence anchor")
        chunks.append(
            SourceChunk(
                chunk_id=row["chunk_id"],
                evidence_id=row["evidence_id"],
                doc_id=row["doc_id"],
                source_id=source_id,
                content=row["content"],
                data_release_id=DATA_RELEASE_ID,
                product_line=annotation.get("product_line") or row["stored_product_line"] or "any",
                visibility_scope=row["visibility_scope"] or "internal",
                annotations={
                    "entities": annotation["entities"],
                    "relations": annotation["relations"],
                },
                section_path=row["section_path"],
                page_no=row["page_no"],
                title=row["title"],
                bbox=row["bbox"],
                source_url=row["source_url"],
                doc_version=row["doc_version"],
            )
        )
    if missing_sources:
        raise RuntimeError(
            "active knowledge sources are missing reviewed graph annotations: "
            + ", ".join(sorted(missing_sources))
        )
    if not chunks:
        raise RuntimeError("active Capstone knowledge snapshot produced no graph chunks")
    return chunks


def graph_stage(root: Path) -> dict[str, Any]:
    chunks = load_capstone_graph_chunks(root)
    result = build_graph(chunks, graph_release_id=GRAPH_RELEASE_ID)
    persist_graph_build(
        dsn(),
        result,
        chunks,
        index_release_id=INDEX_RELEASE_ID,
    )
    output = root / "reports" / "capstone" / "graph-build.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": result.status,
        "entities": len(result.entities),
        "edges": len(result.edges),
        "communities": len(result.communities),
        "source_chunks": len(chunks),
        "data_release_ids": sorted(result.data_release_ids),
    }


async def release_stage() -> dict[str, Any]:
    body = {
        "release_id": RELEASE_ID,
        "data_release_id": DATA_RELEASE_ID,
        "index_release_id": INDEX_RELEASE_ID,
        "prompt_release_id": PROMPT_RELEASE_ID,
        "graph_release_id": GRAPH_RELEASE_ID,
        "eval_run_id": "eval-capstone-smoke-v1",
        "services": {"rag_api": "1.0.0", "tool_api": "1.0.0", "copilot_api": "1.0.0"},
    }
    stable = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = "sha256:" + hashlib.sha256(stable.encode()).hexdigest()
    conn = await asyncpg.connect(dsn())
    try:
        async with conn.transaction():
            existing_digest = await conn.fetchval(
                "SELECT manifest_digest FROM governed_release_manifest WHERE release_id = $1",
                RELEASE_ID,
            )
            if existing_digest is not None and existing_digest != digest:
                raise RuntimeError(
                    f"release_id {RELEASE_ID!r} already exists with a different manifest digest; "
                    "use a new CAPSTONE_RELEASE_ID"
                )
            await conn.execute(
                """
                INSERT INTO governed_release_manifest (
                    release_id, environment, manifest_digest, git_sha, created_by,
                    signature_algorithm, manifest_body
                ) VALUES ($1,'dev',$2,$3,'capstone-bootstrap','none',$4::jsonb)
                ON CONFLICT (release_id) DO NOTHING
                """,
                RELEASE_ID,
                digest,
                "0" * 40,
                json.dumps(body),
            )
            await conn.execute(
                """
                INSERT INTO release_environment_pointer (
                    environment, active_release_id, generation, updated_by
                ) VALUES ('dev',$1,1,'capstone-bootstrap')
                ON CONFLICT (environment) DO UPDATE SET
                    active_release_id = EXCLUDED.active_release_id,
                    generation = release_environment_pointer.generation + 1,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                WHERE release_environment_pointer.active_release_id
                      IS DISTINCT FROM EXCLUDED.active_release_id
                """,
                RELEASE_ID,
            )
    finally:
        await conn.close()
    return {"status": "active", "release_id": RELEASE_ID, "manifest_digest": digest}


async def run(root: Path, stage: str, count: int) -> dict[str, Any]:
    root = root.resolve()
    await apply_additive_migrations(root)
    generation = generate(root, count=count)
    summary: dict[str, Any] = {"stage": stage, "generation": generation}
    if stage in {"all", "ingest"}:
        summary["ingest"] = await ingest_stage(root, generation)
    if stage in {"all", "knowledge"}:
        summary["knowledge"] = await asyncio.to_thread(knowledge_stage, root, generation)
    if stage in {"all", "analytics"}:
        summary["analytics"] = await asyncio.to_thread(analytics_stage, root)
    if stage in {"all", "graph"}:
        summary["graph"] = await asyncio.to_thread(graph_stage, root)
    if stage in {"all", "release"}:
        summary["release"] = await release_stage()
    output = root / "reports" / "capstone" / f"bootstrap-{stage}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stage", choices=["all", "ingest", "knowledge", "analytics", "graph", "release"], default="all")
    parser.add_argument("--ticket-count", type=int, default=int(os.environ.get("CAPSTONE_TICKET_COUNT", "240")))
    args = parser.parse_args()
    summary = asyncio.run(run(args.root, args.stage, args.ticket_count))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
