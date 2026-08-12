# 里程碑验收口径 · resolution_rate（工单解决率）

> 文档定位：新指标「从源头走到能被安全调用」的 Done 定义与风险边界  
> 指标：`resolution_rate`（整体解决率，≠ `first_resolution_rate` 一次解决率）  
> 最后更新：2026-08-12

---

## 1. 指标精确口径（无歧义）

| 项 | 定义 |
|----|------|
| **指标名** | `resolution_rate` |
| **业务名** | 工单解决率 |
| **分子** | 同一分母集合内，`status ∈ {resolved, closed}` 的工单数（staging 标志 `is_resolved = true`，对应 intermediate 字段 `resolved_ticket_count`） |
| **分母** | **当天创建**的工单数（`created_date` / `metric_date` 口径，字段 `ticket_count`）。**不是**「当天处理/活跃」的工单 |
| **公式** | `resolved_ticket_count / nullif(ticket_count, 0)`；分母为 0 时结果为 `NULL`，不产出伪 0/Inf |
| **类型** | `ratio`，合法值域 `[0, 1]` |
| **主维度** | `metric_date × product_line`（可再切 `priority` / `org_id` / `category`，与注册表 `allowed_dimensions` 一致） |
| **与 first_resolution_rate 区别** | 本指标看「最终是否解决」；`first_resolution_rate` 看「是否一次解决（未升级代理口径）」 |

**不可执行红线（口径相关）**

- 禁止把分母偷换成「当天更新/活跃工单」而不改注册表与验收文档。
- 禁止在 mart 绕过 intermediate 硬算分子分母，破坏分层。
- Agent / BI **禁止**直接拼 SQL 访问 `ticket_fact` / raw 表；只能经 `query_support_kpis_v1` → `agent_tool_input_view`。

---

## 2. PII 分级

| 对象 | PII 等级 | 理由 |
|------|---------|------|
| 聚合指标 `resolution_rate` 本身 | **none** | 仅比率，不含可定位个人的标识 |
| Agent 可查视图 `agent_tool_input_view` | **none / 仅安全维度** | 只暴露 `metric_date`、`product_line`、`priority`、`org_id`、`category`、`metric_value` 等；**禁止**出现 `customer_id`、`assignee_id`、`subject`、`description`、联系方式 |

结论：Agent 拿到的只能是聚合结果。一旦视图出现 `customer_id`，PII 测试 `no_pii_columns_in_agent_tool_input_view` 必须失败并拦截上线。

---

## 3. 可查角色与 HITL

| 角色 | 可否查询 `resolution_rate` |
|------|---------------------------|
| `support_ops` | ✅ |
| `instructor` | ✅ |
| `admin` | ✅ |
| 其他（如 `end_user` / `support_agent` 未在注册表） | ❌，返回 `denial_code`（如 `ROLE_DENIED` / `METRIC_DENIED`） |

**需人工介入（HITL）的节点**

- 调用方请求注册表外指标/维度/过滤器 → **直接拒绝**（非静默降级）。
- 查询结果用于对外承诺 SLA/客户沟通且置信不足或与看板严重不一致 → 运营人工复核后再引用。
- 任何试图绕过工具契约、要求裸 SQL 访问原始工单 → **拒绝 + 审计**。

---

## 4. 工程 Done（与现网 11 指标同资质）

- [ ] 契约字段口径对齐（`status` / `product_line` / `created_at`）
- [ ] 入湖可补数、幂等不写重
- [ ] Iceberg 快照可时间旅行对比
- [ ] dbt intermediate → mart → safe_view 四处白名单开口
- [ ] `metric_registry_v1.yml` 完整登记（含分子分母）
- [ ] 工具契约允许查询；越权有 `denial_code`；审计留痕
- [ ] dbt test + Week05 集成/契约测试通过
