# Prediction Protocol

## Schema

每条预测是一行 JSONL：

```json
{
  "case_id": "string",
  "aspect": "string",
  "prediction": { /* aspect-specific fields */ },
  "reasoning": "string — code-path-level justification",
  "confidence": 0.0-1.0,
  "risks": ["string — explicit failure modes"]
}
```

`aspect` 由项目绑定层定义（见 `bindings/`），但通用协议对 prediction
的内容有以下约束。

## Hard Rules (R1-R5)

这 5 条规则在所有项目中通用。违反任何一条视为 PTV 协议违规。

### R1: Prediction Must Reflect Actual System Behavior

预测必须反映你对当前系统行为的判断，**不是**你期望的正确答案。

```
❌ "prediction": {"top1_ok": true, "root_cause_type": "stockout"}
   reasoning: "ground truth expects stockout"

✅ "prediction": {"top1_ok": false, "root_cause_type": null}
   reasoning: "RootCauseType enum does not contain 'interaction_channel_category',
   runtime will raise ValueError at policy_registry.py:42"
```

### R2: Reasoning Must Reference Code Paths

reasoning 字段必须包含具体的代码引用（文件名、函数名、行号）。
模板化或空泛的 reasoning 是协议违规。

```
❌ reasoning: "System should handle this case correctly."

✅ reasoning: "plan_compiler._parallel_broad_contribution_chains() at line 305
   compiles 2 chains for dimensions=['channel','category'], but
   interaction_multiplier() only affects channel UV. E1 detection will
   fire for campaign_traffic_drop, not interaction."
```

### R3: Predictions Must Differ Between Rounds

如果当前轮次的 predictions 和上一轮完全相同（diff 为空），说明你没有
更新对系统的理解——这是协议违规。

每轮 fix 后，至少以下预测必须变化：
1. 被修复 case 的 prediction 字段
2. 被修复 case 的 reasoning 字段
3. confidence 值（反映 fix 对结果的影响估计）

### R4: Risks Must Be Actionable

risks 数组中的每一项必须是一个可以导致该预测失败的具体场景，
不能是空字符串或 generic placeholder。

```
❌ risks: ["might fail"]

✅ risks: [
     "E1 z_score for interaction UV drop may fall below SIGNIFICANCE_THRESHOLD=2.0",
     "contribution_set_builder.merge() dedup key does not include 'element' for IX cases"
   ]
```

### R5: New-Case Predictions Are Mandatory

新增 eval case 后的第一轮 PTV，必须为每个新 case 写预测。
不能只预测旧 case 而跳过新 case。

## Multi-Round Prediction Update Rules

```
Round 1:
  Write predictions for ALL cases from scratch.
  Base predictions on reading current code.

Round N > 1:
  Read previous gap_report.json.
  For each gap:
    - If you fixed the underlying issue: update prediction to pass
    - If you did NOT fix it: keep prediction as fail with updated reasoning
    - If gap was overfit: reassess whether your fix made things worse
  For unchanged cases:
    - Keep prediction but update confidence if ambient changes may affect it
```

## Validation

预测文件必须通过项目的 prediction validator（exit code = 0）才能
进入 EXECUTE 步骤。验证器由项目绑定层定义。

通用验证规则（所有项目适用）：
1. 每行必须是合法 JSON
2. case_id 和 aspect 必须是非空字符串
3. prediction 必须是 dict
4. reasoning 非空
5. risks 至少 1 项，每项非空
6. confidence 在 [0.0, 1.0] 范围内
