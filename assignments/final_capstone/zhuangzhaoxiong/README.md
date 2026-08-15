# Final Capstone · Webhook 问题处理方案卡

**学员**：庄昭雄（GitHub: [173787247](https://github.com/173787247)）  
**主题**：B. Webhook 错误排查  
**分支**：[`homework/final-capstone-webhook-zhuangzhaoxiong`](https://github.com/173787247/omnisupport-copilot/tree/homework/final-capstone-webhook-zhuangzhaoxiong)

## 验收摘要

```text
baseline_commit: 1473db6
candidate_commit: 81a87a6
theme: webhook-troubleshooting
provider/model: deterministic fallback | ollama optional
release_id: capstone-v1.0.0
data_release_id: data-capstone-v1
index_release_id: index-capstone-v1
golden_set: 8 cases, 8 passed (offline evaluator pass_rate=1.0)
contract_tests: 4 passed
hard_gates: contract + offline eval + live handling-plan + confirm/HITL
capstone_e2e: pass（verify_e2e，含 Phoenix RAG + HITL）
representative_trace_id: abe6a7ee5b9cad424ac4382cd9d1db5a
live_evidence: reports/live_evidence.json
  C1 confirm+citations; C3 clarify; C6 hitl
  note completed->cached; credit awaiting_approval->completed
bootstrap_idempotency: 2nd run tickets skipped=240; chunks skipped=125
known_limitations:
  1) 本地 Compose ≠ 生产 HA/多租户密钥隔离强度
  2) 方案卡步骤抽取对 fallback 文本敏感，真实 LLM 质量需 Ollama/云模型复核
  3) 未新建 Tool，复用 add_internal_note / grant_service_credit 控制面
```

## 一句话

在现有 OmniSupport Capstone 上新增小型 Webhook 知识包，并通过 Product API 输出「问题处理方案卡」：有证据才给诊断与 ≤3 步；不足则澄清/拒答；低风险备注需确认，财务授信必须 HITL；用 Golden Set、契约测试与 Release/回滚说明证明可上线。

## 快速导航（本作业包）

| 文档 | 链接 |
|------|------|
| 架构设计 | [design/architecture.md](./design/architecture.md) |
| 方案卡 Schema | [contracts/handling_plan_card.schema.json](./contracts/handling_plan_card.schema.json) |
| Skill 说明 | [contracts/skill_handling_plan_card.md](./contracts/skill_handling_plan_card.md) |
| Golden Set | [evals/golden_set.jsonl](./evals/golden_set.jsonl) |
| 知识文档 401 | [data/workspace-webhook-signature-401.html](./data/workspace-webhook-signature-401.html) |
| 知识文档 retry/dedup | [data/workspace-webhook-retry-dedup.html](./data/workspace-webhook-retry-dedup.html) |
| Manifest | [data/manifest_webhook_final_pack.json](./data/manifest_webhook_final_pack.json) |
| Baseline | [reports/baseline.md](./reports/baseline.md) |
| Eval 报告 | [reports/eval_report.md](./reports/eval_report.md) |
| Live 证据 | [reports/live_evidence.json](./reports/live_evidence.json) |
| Offline E2E JSON | [reports/e2e_report.json](./reports/e2e_report.json) |
| Release/回滚 | [reports/release_and_rollback.md](./reports/release_and_rollback.md) |
| 演示稿 | [demo/demo_script.md](./demo/demo_script.md) |
| 反思 | [reflection.md](./reflection.md) |

## 仓库内对应实现（同分支）

| 内容 | 链接 |
|------|------|
| 产品侧 Schema | [`contracts/product/handling_plan_card.schema.json`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/contracts/product/handling_plan_card.schema.json) |
| 方案卡构建 | [`services/copilot_api/app/handling_plan.py`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/services/copilot_api/app/handling_plan.py) |
| Product API 端点 | [`services/copilot_api/app/main.py`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/services/copilot_api/app/main.py) |
| 知识包（入库） | [`data/capstone/knowledge/`](https://github.com/173787247/omnisupport-copilot/tree/homework/final-capstone-webhook-zhuangzhaoxiong/data/capstone/knowledge) |
| Offline 评测 | [`scripts/capstone/eval_handling_plan.py`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/scripts/capstone/eval_handling_plan.py) |
| Live 证据采集 | [`scripts/capstone/collect_live_evidence.py`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/scripts/capstone/collect_live_evidence.py) |
| 契约测试 | [`tests/contract/test_handling_plan_card.py`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/tests/contract/test_handling_plan_card.py) |
| Golden Set（仓库） | [`evals/sets/final_capstone_webhook_plan_v1.jsonl`](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/evals/sets/final_capstone_webhook_plan_v1.jsonl) |

## 快速启动

```bash
cp infra/env/.env.example infra/env/.env.local
docker compose --env-file infra/env/.env.local -f infra/docker-compose.yml up -d --build
docker compose --profile capstone --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm capstone_bootstrap
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  pytest tests/contract/test_handling_plan_card.py -q
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  python -m scripts.capstone.eval_handling_plan
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  python -m scripts.capstone.verify_e2e
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm \
  -e LIVE_EVIDENCE_HOST=0 -e PRODUCT_API_URL=http://copilot_api:8002 \
  -e PYTHONPATH=/workspace:/workspace/services/copilot_api \
  --workdir /workspace devbox python -m scripts.capstone.collect_live_evidence
```

可选真实模型（Ollama）：

```bash
# infra/env/.env.local
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:14b
LLM_BASE_URL=http://host.docker.internal:11434/v1
QUERY_REWRITE_STRATEGY=llm
QUERY_REWRITE_PROVIDER=ollama
QUERY_REWRITE_MODEL=qwen3:4b
```

## 方案卡 API

`POST /api/v1/handling-plan`（需登录 Product API）

返回契约见作业包 [contracts/handling_plan_card.schema.json](./contracts/handling_plan_card.schema.json)，以及仓库正式契约 [contracts/product/handling_plan_card.schema.json](https://github.com/173787247/omnisupport-copilot/blob/homework/final-capstone-webhook-zhuangzhaoxiong/contracts/product/handling_plan_card.schema.json)。
