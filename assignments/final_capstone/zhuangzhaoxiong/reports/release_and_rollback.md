# Release and Rollback

## Candidate binding（本机实测）

```text
release_id: capstone-v1.0.0
data_release_id: data-capstone-v1
index_release_id: index-capstone-v1
prompt_release_id: prompt-capstone-v1
skill/service: copilot_api /api/v1/handling-plan
manifest_digest: sha256:9f299a590c0caeec52598e983d6767c4d2dd76f8e68eed3c752f5b9e5fa691dd
```

方案卡与知识包通过 Capstone bootstrap 进入同一 data/index release；产品服务版本随镜像/提交发布。

## Gates（已绿）

1. contract：`test_handling_plan_card.py` → 4 passed
2. offline eval：`eval_handling_plan.py` → pass_rate = 1.0（8/8）
3. security：C6 HITL；无自动 grant；模糊问句澄清
4. regression：`python -m scripts.capstone.verify_e2e` → pass（含 Phoenix HITL）
5. live actions：`reports/live_evidence.json` note 幂等 + credit 审批恢复
6. bootstrap 幂等：二次运行 tickets `inserted=0/skipped=240`，chunks `skipped=125`

## Activation（参考 Week14）

```bash
python -m release.registry register --manifest <manifest.json>
python -m release.registry promote --release-id capstone-v1.0.0 --actor zhuangzhaoxiong
```

Capstone 本地也可由 bootstrap 写 pointer（dev `signature_algorithm=none`）。本机 bootstrap 已将 `capstone-v1.0.0` 置为 `active`。

## Rollback

```bash
python -m rollout.rollback \
  --current-release-id capstone-v1.0.0 \
  --target-release-id <previous-active> \
  --actor incident-commander \
  --reason final_capstone_rollback_drill
```

回滚后验证：

1. 原 Capstone E2E 仍 pass
2. 新知识包/新端点按预期关闭或回退到旧 pointer
3. 记录 C8 回归结果

## Verification checklist

- [x] promote 前 gates 全绿（contract / offline / live / e2e）
- [x] 二次 bootstrap 不倍增票据与 chunk
- [x] live HITL resume 成功（见 live_evidence.json）
- [ ] 正式回滚演练目标为直接前一版（课堂演示时执行 `rollout.rollback`）
