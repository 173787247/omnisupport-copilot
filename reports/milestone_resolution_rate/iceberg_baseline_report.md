# Week04 Iceberg Baseline Report

generated_at: `2026-08-12T08:08:00.978799+00:00`

| table | rows | snapshots | files | avg file size | latest operation |
|---|---:|---:|---:|---:|---|
| bronze.raw_ticket_event | 500 | 1 | 1 | 81105.0 | append |
| bronze.raw_doc_asset | 0 | 0 | 0 | 0.0 | None |
| silver.ticket_fact | 500 | 5 | 1 | 28748.0 | append |
| silver.knowledge_doc | 0 | 0 | 0 | 0.0 | None |

## Known Limits

- Week04 records current table health and metadata shape; it does not run compaction.
- Partition distribution is omitted for unpartitioned Student Core Pack tables.

## Next Steps

- Use this report as the before/after baseline for Week05 transform and Week06 orchestration.
- Only introduce maintenance jobs after table growth justifies them.
