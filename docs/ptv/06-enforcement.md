# Enforcement — 反作弊与对抗性审查

## 为什么需要 Enforcement

Codex（或任何自主执行 PTV 的 agent）有动机走捷径：跳过诊断、
抄写 ground truth、重复运行不改代码。Enforcement 层的存在是为了
让这些捷径不可能通过审查。

## 自动检测规则

以下 5 项可以通过脚本或审查者机械检查，不需要理解代码逻辑：

### DETECT-1: Prediction == Ground Truth

```bash
CYCLE=eval_out/ptv/cycle-{id}
ROUND=$CYCLE/round-{N}

# 检查 predictions.jsonl 中的 prediction 字段是否和 ground truth 重合
diff <(jq -S '.prediction' $ROUND/predictions.jsonl | sort) \
     <(jq -S '{root_cause_type, dimension, element}' ground_truth.jsonl | sort)
```

如果高度重合（>80% 的 case 完全匹配），标记为 **PTV_FAKE**。

### DETECT-2: Predictions 跨轮次无变化

```bash
diff $CYCLE/round-{N}/predictions.jsonl $CYCLE/round-{N-1}/predictions.jsonl
# 或检查 prediction_diff.json:
jq '.changed_count' $CYCLE/round-{N}/prediction_diff.json
# changed_count == 0 → PTV_STALE
```

如果 diff 为空，标记为 **PTV_STALE**。

### DETECT-3: 无代码变更的重复 eval

```bash
# 检查每轮的 fix_commit.txt 是否存在
cat $CYCLE/round-{N}/fix_commit.txt
# 或从 git log 验证:
git log --oneline HEAD~{N}..HEAD | grep "fix(.*ptv"
```

如果两个 PTV round 之间没有 fix commit，标记为 **PTV_NOFIX**。

### DETECT-4: 缺少 diagnosis.jsonl

```bash
# 如果 gap_report 中有 non-correct divergence 但无 diagnosis:
jq '.gaps[] | select(.divergence != "correct")' $ROUND/gap_report.json | head -1
ls $ROUND/diagnosis.jsonl
```

如果 gap_report 中有非 correct 的 divergence 但缺少 diagnosis.jsonl，
标记为 **PTV_NODIAG**。

### DETECT-5: Templated Reasoning

```bash
jq -r '.reasoning' $ROUND/predictions.jsonl | sort -u | wc -l
```

如果 unique reasoning 数量远少于 case 数量（ratio < 0.5），
标记为 **PTV_TEMPLATE**。

### DETECT-6: Missing Controller Rule Fields (round > 1)

```bash
# optimization_summary.json 必须包含 controller_rules_applied
jq '.controller_rules_applied' $ROUND/optimization_summary.json
# 缺少 rule_c* 字段 → PTV_NORULES
```

如果 round > 1 的 optimization_summary.json 中缺少
`controller_rules_applied` 字段，标记为 **PTV_NORULES**。

### DETECT-7: Fix Category Stall

```bash
# 检查连续 3 轮是否使用了同一 fix_category
for r in round-01 round-02 round-03; do
  jq -r '.controller_decision.selected_fix_category' $CYCLE/$r/optimization_summary.json
done | uniq -c | awk '$1 >= 3 {print "PTV_STALL: " $2}'
```

如果同一 fix_category 连续出现 ≥3 轮，标记为 **PTV_STALL**。

## 对抗性审查清单

人工审查者（通常是 Claude Code 在 session 后执行）使用以下清单：

```
PTV INTEGRITY:
□ predictions.jsonl reasoning 是 case-specific 的（不是模板化的）
□ predictions 在不同 PTV round 之间有变化（diff 非空）
□ diagnosis.jsonl 对每轮每个 failing case 都有条目
□ 每个 PTV round 之间有至少一个代码变更 commit
□ ptv_trajectory.jsonl 记录了 prediction accuracy
□ 没有 complexity_gap 没有对应 diagnosis entry 的情况
□ prediction reasoning 引用了具体的代码路径（文件名 + 函数名）
□ fix commit message 包含 fix category 标签
□ 没有连续 3 轮相同的 fix category（RULE-C5, DETECT-7）

CONTROLLER DECISION RULES (round > 1):
□ optimization_summary.json 包含 controller_rules_applied 字段
□ 回退时写了 revert_assessment (RULE-C4)
□ 被回退的 fix_category 在 rule_c1_blocked_categories 中
□ deferred ≥2 轮的 category 被 promote 或 escalate (RULE-C2)
□ FIX-D 和 FIX-A 同时存在时优先选择了 FIX-D (RULE-C3)
□ stall_analysis 存在于 ptv_trajectory.jsonl (Analyst 产出)
□ Controller 的 selected_fix_category 没有违反任何 RULE-C

STALL DETECTION:
□ 没有连续 ≥2 轮相同 fix_category 但指标回退的情况
□ 没有 fix_category 被连续 defer ≥3 轮
□ Analyst 的 stall_analysis.recommended_next_category 被 Controller
  采纳，或 Controller 写了为什么拒绝的 justification
```

## 违规处理

| 标记 | 严重程度 | 处理 |
|------|---------|------|
| PTV_FAKE | CRITICAL | 整个 PTV cycle 无效，必须从 round 1 重做 |
| PTV_STALE | HIGH | 当前 round 无效，需要更新 prediction 后重做 |
| PTV_NOFIX | HIGH | 当前 round 无效，需要先实现 fix |
| PTV_NODIAG | MEDIUM | 补写 diagnosis 后可继续 |
| PTV_TEMPLATE | HIGH | 需要重写 reasoning 后重做当前 round |
| PTV_NORULES | HIGH | 补写 controller_rules_applied 后可继续 |
| PTV_STALL | HIGH | 必须切换 fix_category 并 revert 回退 round 的 commit |

## 在 Codex Dispatch Prompt 中的执行

在 dispatch prompt 中嵌入反作弊规则的方式：

```text
ANTI-PATTERN DETECTION (automatic rejection):
1. If prediction.{root_cause_type,dimension,element} == ground_truth → REJECT
2. If predictions_round_N == predictions_round_{N-1} → REJECT
3. If no fix commit between rounds → REJECT
4. If reasoning is identical across >50% of cases → REJECT
5. If diagnosis.jsonl missing for any round with failures → REJECT
6. If optimization_summary missing controller_rules_applied (round > 1) → REJECT
7. If same fix_category applied ≥3 consecutive rounds → REJECT

CONTROLLER DECISION RULES (round > 1):
C1. Regressed fix_category → BLOCKED for next round
C2. Deferred ≥2 rounds → MANDATORY promotion
C3. FIX-D present + FIX-A present → FIX-D first
C4. ≥2 metrics regressed → revert_assessment REQUIRED
C5. Same category >2 consecutive rounds → SWITCH
```

这些规则应该直接放在 dispatch prompt 中，让 Codex 在执行前就知道
什么是不允许的。
