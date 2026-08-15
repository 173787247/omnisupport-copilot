# Baseline

- **repo**: omnisupport-copilot
- **baseline_commit**: `1473db6` (`main` @ merge: productionize governed query rewrite)
- **candidate_branch**: `homework/final-capstone-webhook-zhuangzhaoxiong`
- **theme**: webhook-troubleshooting

## 基线能力（复用，不重做）

- Capstone bootstrap / E2E：`scripts/capstone/bootstrap.py`、`verify_e2e.py`
- 已有 webhook 文档：`data/capstone/knowledge/workspace-api-webhook.html`
- 既有动作：`add_internal_note`（确认）、`grant_service_credit`（HITL）
- Query Rewrite + Hybrid RAG + Phoenix

## 本候选新增

- 2 份知识文档：signature-401、retry-dedup
- Product 契约与端点：`handling_plan_card` / `/api/v1/handling-plan`
- Golden Set 8 条 + offline evaluator
- 作业交付目录：`assignments/final_capstone/zhuangzhaoxiong/`

## 基线命令

```bash
docker compose --env-file infra/env/.env.local -f infra/docker-compose.yml up -d --build
docker compose --profile capstone --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm capstone_bootstrap
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  python -m scripts.capstone.verify_e2e
```

预期：`reports/capstone/e2e-verification.json` 顶层 `status=pass`；二次 bootstrap 不翻倍。

本候选已保存到作业包：

- [`e2e_verification.json`](./e2e_verification.json)
- [`bootstrap_second_run.json`](./bootstrap_second_run.json)
- [`rollback_drill.json`](./rollback_drill.json)
