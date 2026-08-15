# Reflection · 庄昭雄

## 1. 为什么不是普通聊天框？

它在坐席决定「下一步做什么」的决策点提供结构化、可审计输出，并把副作用动作接到确认/HITL，而不是自由文本建议。

## 2. 必须拒答/澄清的问题

缺少错误码的「webhook not working」。若硬答，可能误导坐席去做批量重放或密钥重置，造成重复扣费或安全事件。

## 3. Query Rewrite 的收益与约束

收益：保留 `WS-WEBHOOK-401` 等精确标识，提升 lexical 命中。约束：不允许删改受保护标识；失败则 fallback，并在 debug 暴露 `fallback_reason`。

## 4. 引用不支持结论为何算失败？

因为客户操作会被错误证据推动。评测要求 citation 的 `evidence_id` 必须来自检索结果，禁止手写伪引用。

## 5. 为何确认 + 幂等，为何 HITL 不能靠 Prompt？

确认防止误点；幂等防止网络重试双写。HITL 由 Tool/Product 代码与契约强制，Prompt 可被忽略或注入绕过。

## 6. 版本不一致的“随机”问题

旧索引 + 新 Prompt 会出现“偶发找不到新文档/偶发拒答”。所以 release 必须绑定 data/index/prompt/service。

## 7. Bad-case 示例（设计态）

含糊问题若被模型硬答成高置信步骤 → 根因是缺澄清门禁 → 修复：`needs_clarification` + 无 evidence 不强行 steps → 回归：C3。

## 8. 本地到生产缺口

- 安全：KMS/密钥轮换审计、更强租户隔离
- 可靠性：多副本、熔断与跨区 failover
- 容量：检索/生成队列与限流
- 运维：报警、on-call、变更窗口

## 9. 若再有两周

优先做 **线上同口径评测门禁接入 Canary**（收益：挡住坏 release；风险：评测噪声；成本：维护 8–20 条金标），而不是堆更多主题文档。

补充：本作业已加 Console 方案卡 UI 与 OpenAPI 入口，作为“结构化契约可见性”加分项；生成质量仍建议接 Ollama 复核。
