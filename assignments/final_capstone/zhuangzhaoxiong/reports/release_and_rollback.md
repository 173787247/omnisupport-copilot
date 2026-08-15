# Release and Rollback

## Candidate binding（示例）

```text
release_id: capstone-final-webhook-v1
data_release_id: data-capstone-v1
index_release_id: index-capstone-v1
prompt_release_id: prompt-capstone-v1
skill/service: copilot_api handling-plan endpoint
```

方案卡与知识包通过 Capstone bootstrap 进入同一 data/index release；产品服务版本随镜像/提交发布。

## Gates

1. contract：`test_handling_plan_card.py`
2. offline eval：`eval_handling_plan.py` pass_rate = 1.0
3. security：C6 HITL；无自动 grant
4. regression：`python -m scripts.capstone.verify_e2e`

## Activation（参考 Week14）

```bash
python -m release.registry register --manifest <manifest.json>
python -m release.registry promote --release-id <candidate> --actor zhuangzhaoxiong
```

Capstone 本地也可由 bootstrap 写 pointer（dev `signature_algorithm=none`）。

## Rollback

```bash
python -m rollout.rollback \
  --current-release-id <candidate> \
  --target-release-id <previous> \
  --actor incident-commander \
  --reason final_capstone_rollback_drill
```

回滚后验证：

1. 原 Capstone E2E 仍 pass
2. 新知识包/新端点按预期关闭或回退到旧 pointer
3. 记录 C8 回归结果

## Verification checklist

- [ ] promote 前 gates 全绿
- [ ] rollback 目标是直接前一版
- [ ] 回滚后 `verify_e2e` pass
- [ ] 报告保留 before/after release_id
