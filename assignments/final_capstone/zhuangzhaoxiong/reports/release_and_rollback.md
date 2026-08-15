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
4. regression：`verify_e2e` → pass（见 `e2e_verification.json`）
5. live actions：`live_evidence.json` note 幂等 + credit 审批恢复
6. bootstrap 幂等：`bootstrap_second_run.json` tickets skipped=240；chunks skipped=125

## Activation

本机 bootstrap 已将 `capstone-v1.0.0` 置为 `dev` active pointer（`signature_algorithm=none`）。

## Rollback drill（已执行）

机器证据：[`rollback_drill.json`](./rollback_drill.json)

```text
before:  capstone-v1.0.0 (generation=1)
promote: capstone-final-webhook-candidate-v1 (generation=2, direct previous=capstone-v1.0.0)
rollback:capstone-v1.0.0 (generation=3)
audit_event_id: 527e16fc-1079-44d0-b803-d8848d4d6585
verified_active_restored: true
```

等价 CLI 语义（Week14）：

```bash
python -m rollout.rollback \
  --current-release-id capstone-final-webhook-candidate-v1 \
  --target-release-id capstone-v1.0.0 \
  --actor incident-commander \
  --reason final_capstone_rollback_drill
```

说明：Capstone 使用简化 manifest + `signature_algorithm=none` 的 dev pointer；
演练脚本 `scripts/capstone/final_score_pack.py` 按同一指针原子切换模型执行 promote→rollback，
并写入 immutable `release_audit_event`。

回滚后验证：

1. active pointer 回到 `capstone-v1.0.0`
2. handling-plan C1/C3/C8 smoke 通过（见 rollback_drill.json）
3. Capstone `verify_e2e` 保持 pass（见 e2e_verification.json）

## Verification checklist

- [x] promote 前 gates 全绿（contract / offline / live / e2e）
- [x] 二次 bootstrap 不倍增票据与 chunk
- [x] live HITL resume 成功（见 live_evidence.json）
- [x] 正式回滚演练：candidate → previous，active 恢复并回归 smoke pass
