# 设计说明 · Webhook 问题处理方案卡

## 1. 解决谁的什么问题

Northstar 坐席在处理 Workspace Webhook 故障时，不只需要“聊天回答”，还需要一张可审计的**处理方案卡**：诊断摘要、有序步骤、证据引用、置信度、是否澄清/拒答、以及下一步动作控制（确认 / HITL）。

这不是普通聊天框，因为：

- 证据不足必须澄清或拒答，而不是编造根因
- 精确错误码（如 `WS-WEBHOOK-401`）受 Query Rewrite / 方案卡标识保护
- 副作用动作走既有 Tool 契约与 HITL，而不是 Prompt 提醒

## 2. 非目标

- 不重做前端、不新建向量库、不训练模型
- 不新增高风险 Tool；复用 `add_internal_note` / `grant_service_credit`
- 不把 deterministic fallback 宣称为真实 LLM 质量

## 3. 主链

```text
合成知识 HTML + manifest
  → doc_ingest / parse / chunk / index（既有 Capstone）
  → Product API /handling-plan
  → RAG /rag/answer（rewrite + hybrid + citations）
  → handling_plan builder（结构化契约 + 动作策略）
  → 可选 cases/{id}/actions（confirm / HITL）
  → Golden Set / Phoenix / Release pointer
```

## 4. 边界案例

| 场景 | 行为 |
|------|------|
| 正常 401 签名问题 | 给 ≤3 步 + citation + note/confirm |
| 缺错误码的含糊问题 | `needs_clarification=true` |
| 无检索命中 | `abstain_reason` 非空，steps=[] |
| 批量重放/特权变更 | `operation=none`，禁止自动执行 |
| 服务补偿 | `grant_service_credit` + `control=hitl` |

## 5. 关键取舍

1. **方案卡放 Product API 而不是改 RAG schema**：RAG 继续只负责 grounded answer；产品层负责动作策略与输出契约，符合既有分层。
2. **步骤从答案抽取而非再调一次 LLM**：保证 fallback 模式也可结构化；真实模型可加分但不是硬门槛。
3. **财务与批量操作硬编码策略**：权限不靠 Prompt。

## 6. 与课程周次映射

Week01–03 契约/幂等；Week07–08 RAG/Rewrite；Week09–10 Skill/HITL；Week11–12 Eval/Trace；Week14–15 Release/回滚。
