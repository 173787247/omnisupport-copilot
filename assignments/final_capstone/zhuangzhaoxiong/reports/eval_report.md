# Evaluation Report

## Scope

- dataset: `evals/sets/final_capstone_webhook_plan_v1.jsonl`（8 cases, C1–C8）
- offline evaluator: `python -m scripts.capstone.eval_handling_plan`
- contract tests: `pytest tests/contract/test_handling_plan_card.py`

## Metrics / thresholds

| 维度 | 指标 | 目标 |
|------|------|------|
| 正确性 | case pass_rate | ≥ 80%（本作业目标 100% offline） |
| 证据 | citation 出现时必须有 evidence_id | 100% |
| 安全 | 高风险绕过数 | 0 |
| 标识 | required_identifiers 保留 | 100% |

## How to reproduce

```bash
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  pytest tests/contract/test_handling_plan_card.py -q
docker compose --profile tools --env-file infra/env/.env.local -f infra/docker-compose.yml run --rm devbox \
  python -m scripts.capstone.eval_handling_plan \
  --out reports/capstone/final_webhook_plan_eval.json
```

将 JSON 结果复制/链接到本目录的 `e2e_report.json` 或直接引用仓库 `reports/capstone/`。

## Case intent map

| ID | 预期 |
|----|------|
| C1 | 正常签名恢复 + citation |
| C2 | WS-WEBHOOK-401 / HTTP 401 标识保留 |
| C3 | 歧义澄清 |
| C4 | 批量重放不可自动执行 |
| C5 | 内部备注 confirm |
| C6 | 授信 HITL |
| C7 | fallback 仍安全 |
| C8 | 旧 webhook 文档回归 |

## Bad-case template（直播时填）

```text
case_id: <id>
observed: <what happened>
expected: <what should happen>
trace_id: <Phoenix id>
failed_stage: rewrite | retrieve | generate | policy
root_cause: <evidence-backed>
fix: <smallest change>
regression_test: <case added>
residual_risk: <what remains>
```

## Live Trace

完整 Compose + RAG 拉起后，从 `/api/v1/handling-plan` 响应读取 `trace_id`，到 http://localhost:6006 定位 `product.handling_plan` span。
