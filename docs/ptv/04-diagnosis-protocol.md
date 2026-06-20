# Diagnosis Protocol

## 核心原则

诊断是 PTV 的「显微镜」——它把 gap_report 中的 divergence 分类转化为
可执行的 fix 指令。没有诊断的 PTV 轮次等于「蒙眼修 bug」。

**诊断不可跳过。** 如果某轮 gap_report 有任何非 correct 的 divergence，
必须写 diagnosis.jsonl。即使你认为问题很明显，诊断记录仍然是 GRPO
训练数据的关键组成部分。

## Diagnosis Schema

每行一个诊断条目（JSONL）：

```json
{
  "round": 3,
  "case_id": "MC03_multi_cause_gmv_all_dims",
  "aspect": "outcome",
  "divergence": "design_flaw",
  "diagnosis": "complexity_gap",
  "fix_category": "FIX-ENUM",
  "root_cause_analysis": "Free-text explanation of WHY the system failed",
  "proposed_fix": {
    "description": "Add interaction_channel_category to RootCauseType enum",
    "files": ["metric_rca/domain/enums.py", "metric_rca/business/policy_registry.py"],
    "estimated_impact": "IX01-IX04 will transition from structural-impossible to testable"
  },
  "confidence": 0.9,
  "linked_cases": ["IX01", "IX02", "IX03", "IX04"]
}
```

## Diagnosis Classification

四种 divergence type，含义固定（不随项目变化）：

| divergence | 含义 | 典型原因 |
|-----------|------|---------|
| `correct` | 预测正确 | 不需要诊断 |
| `complexity_gap` | 系统能力不足但架构可扩展 | 缺少 policy, 注入强度不足, 阈值需调整 |
| `design_flaw` | 架构设计缺陷 | 缺少 enum 值, 缺少 tool, 错误的数据模型 |
| `overfit` | 预测比实际更悲观 | 系统表现超预期, 可能是偶然 |

## Fix Category Framework

Fix category 由项目绑定层定义具体列表。通用协议规定：

1. 每个 fix category 必须有唯一的短代码（如 FIX-X）
2. 每个 fix category 必须映射到具体的代码变更类型
3. 必须有一个 `STRUCTURAL` 类别，表示「当前架构无法修复，需要新能力」
4. 每轮 PTV 只应用 **一个** fix category（避免混合变更干扰因果判断）

## Linked Cases

`linked_cases` 字段标记可能受同一 fix 影响的其他 case。用途：
1. 当你 fix 了一个 case，自动更新 linked cases 的预测
2. GRPO 训练时，linked cases 的轨迹可以作为一组来评估
3. 审查时，检查 linked cases 是否真的都被修复了

## Stall Detection (round > 1)

诊断中必须包含自锁循环检测。每条 diagnosis entry 需要额外字段：

```json
{
  "stall_risk": "none | low | high",
  "stall_reason": "FIX-A applied 2 consecutive rounds, this case failed both times"
}
```

**stall_risk 判定规则**：
- `high`：该 case 已连续 ≥2 轮以同一 fix_category 失败
- `low`：该 case 上一轮以同一 fix_category 失败（首次重复）
- `none`：该 case 首次失败，或当前轮的 fix_category 与上一轮不同

Analyst Agent 在 ptv_trajectory.jsonl 中汇总 stall_analysis（见
02-workflow.md Subagent Prompt 模板），Controller 据此做决策。

## Escalation Triggers

以下条件触发升级：

```
IF round > MAX_ROUNDS AND remaining_failures > 0:
  IF all remaining failures have diagnosis = STRUCTURAL:
    → ESCALATE: document the architectural gap
  ELSE:
    → CONTINUE: increase MAX_ROUNDS or re-evaluate fix strategy

IF same case_id fails with same fix_category for 2 consecutive rounds:
    → Controller MUST switch category (see RULE-C1 in 02-workflow.md)
    → If no alternative category exists → ESCALATE

IF same fix_category applied 2 consecutive rounds with net metric regression:
    → MANDATORY category switch + revert assessment (RULE-C1 + RULE-C4)

IF a fix_category has been deferred for ≥2 consecutive rounds:
    → MANDATORY promotion (RULE-C2 in 02-workflow.md)
    → If promotion is blocked by RULE-C1, escalate the deferred target
```

升级输出（项目无关的通用格式）：

```json
{
  "escalation_type": "structural_gap | fix_exhaustion",
  "failing_cases": ["IX01", "IX02"],
  "diagnosis_history": [
    {"round": 1, "fix_category": "FIX-ENUM", "result": "still_failing"},
    {"round": 2, "fix_category": "FIX-P", "result": "still_failing"}
  ],
  "capability_gap": "Free text: what new capability is needed",
  "recommended_next": "Pointer to design doc or new iteration spec"
}
```
