# 8 分钟演示脚本

## 0:00–1:00 问题与边界

说明主题 B：Webhook 排查方案卡。展示 `design/architecture.md` 中的非目标与硬门槛。

## 1:00–2:30 知识包

展示两份新文档与 manifest 生成路径；强调合成数据、PII clear、version 3.2。

## 2:30–4:30 方案卡

调用 `POST /api/v1/handling-plan`（同时打开 Console **Handling plan card** 与 `/docs`）：

1. 正常：`WS-WEBHOOK-401` → steps + citations
2. 歧义：`webhook not working` → clarification/abstain
3. 财务：`grant service credit` → `control=hitl`

## 4:30–6:00 受控动作

复用既有 case action：

- note + idempotency_key → completed，重放不双写
- credit → awaiting_approval → admin resume

## 6:00–7:30 Eval / Trace / Rollback

- 展示 golden set 与 offline eval JSON
- Phoenix 打开 `trace_id`
- 展示 `rollback_drill.json`：candidate → `capstone-v1.0.0` 恢复

## 7:30–8:00 反思一句

“生产级 = 契约 + 失败策略 + 权限 + 评测 + Trace + 回滚，而不是答案看起来很像。”
