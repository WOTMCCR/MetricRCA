# MetricRCA Binding — PTV Project Adaptation

本文件将通用 PTV 协议绑定到 MetricRCA 项目。

## 1. Prediction Aspects

MetricRCA 使用 6 个 prediction aspect：

| Aspect | 必要的 prediction keys | 说明 |
|--------|----------------------|------|
| `intent` | `metric_id` | agent 是否正确解析了用户查询的指标 |
| `execution` | `step_count` 或 `tool_sequence` + `critical_decisions` | agent 执行路径是否符合预期 |
| `evidence` | `chain` | evidence 链是否完整（E1→E2→...→E_rank） |
| `memory` | `influence` | memory 是否污染了结论 |
| `outcome` | `root_cause_type` | 最终答案是否正确 |
| `multi_cause_outcome` | `root_causes`, `top3_ok` | 多因 case 的 cause set 是否完整 |

`multi_cause_outcome` 是 Phase C 扩展，仅用于带 `root_causes` 的多因 case。

### No-anomaly Case 的特殊约束

当 outcome 预测为 no-anomaly 时，execution 预测必须包含：
- `tool_sequence` 中有 `detect_anomaly`
- `forbidden_tools` 中列出所有 RCA 下游工具

## 2. Fix Taxonomy

MetricRCA 定义 11 个 fix category + 1 个升级类别：

| Code | Target | 典型变更 |
|------|--------|---------|
| `FIX-I` | Intent | intent_planner.py, LLM prompt |
| `FIX-G` | Gate | scorer.py gate thresholds |
| `FIX-T` | Tool | agent/tools/*.py |
| `FIX-P` | Prompt | agent prompts, system message |
| `FIX-D` | Discovery | plan_compiler.py drilldown logic |
| `FIX-M` | Merge | contribution_set_builder.py |
| `FIX-A` | Attribution | ranker, contribution calculation |
| `FIX-B` | Baseline | baseline_window.py, date logic |
| `FIX-INJ` | Injection | anomaly_injection.py, seed_data.py |
| `FIX-ENUM` | Enum/Policy | enums.py, policy_registry.py |
| `FIX-S` | Scorer | scorer.py scoring functions (non-gate) |
| `STRUCTURAL` | Architecture | 需要新能力（如 Python sandbox） |

### FIX 分类决策树

```
case failed →
  Is the root_cause_type missing from enum?
    YES → FIX-ENUM
  Is the case reaching the wrong evidence chain?
    YES → FIX-D (discovery) or FIX-I (intent)
  Is the case reaching correct chain but wrong conclusion?
    YES →
      Injection data too weak? → FIX-INJ
      Contribution math wrong? → FIX-A
      Merge logic wrong? → FIX-M
      Ranking wrong? → FIX-A
  Is the eval scorer wrong?
    YES → FIX-S or FIX-G
  None of the above work?
    → STRUCTURAL
```

## 3. Eval Commands

```bash
# Seed data
make seed SEED_PROFILE=regression

# Run eval (streaming)
make eval-stream EVAL_ID=eval-c{round}

# Run gap analysis
make eval-gaps EVAL_ID=eval-c{round}

# Validate predictions
python -m metric_rca.evals.prediction eval_out/eval-c{round}/predictions.jsonl

# Run tests
make test

# Full regression
make eval-regression
```

## 4. Exit Conditions

MetricRCA Phase C 的 PTV 退出条件：

```
Per-family gates:
  single_cause:     28/28 green
  multi_cause:      accuracy >= 0.75 (6/8 cases)
  interaction:      accuracy >= 0.50 (2/4 cases)
  lagged:           accuracy >= 0.50 (1/2 cases)
  weak_signal:      accuracy >= 0.50 (1/2 cases)

Aggregate gates:
  root_cause_set_recall_avg >= 0.85
  weighted_explanation_coverage_avg >= 0.85

Confirmation:
  2 consecutive green runs required to exit
```

## 5. Scoring Metrics

MetricRCA 的 scorer.py 提供以下指标：

| Metric | Type | PTV Role |
|--------|------|----------|
| `intent_ok` | binary | 基础 gate |
| `anomaly_ok` | binary | 基础 gate |
| `dominant_top1_ok` | binary | 单因 accuracy |
| `evidence_coverage` | float [0,1] | evidence 完整性 |
| `sql_safe` | binary | 安全 gate |
| `reflection_repair_ok` | binary | 自修复 gate |
| `report_traceable_ok` | binary | 报告溯源 gate |
| `memory_pollution_ok` | binary | memory 污染 gate |
| `root_cause_set_recall` | float [0,1] | 多因召回率 |
| `root_cause_set_precision` | float [0,1] | 多因精确率 |
| `weighted_explanation_coverage` | float [0,1] | 加权覆盖率 |
| `top3_contains_all_major_causes` | binary | 多因排序 gate |
| `p95_latency_ms` | int | 性能诊断 |
| `p95_sql_count` | int | 性能诊断 |

## 6. PTV Data Consumers (三层消费)

参见 `docs/ptv/05-grpo-bridge.md` 了解三层消费模型的完整定义。

### Layer 1: Agent System Optimization Context

MetricRCA 的 optimization summary 需要包含：

| 字段 | 来源 | 作用 |
|------|------|------|
| `failure_patterns` | gap_report + diagnosis 的 cross-case 聚类 | 驱动下一轮 fix 决策 |
| `effective_fixes_history` | ptv_trajectory 跨 round 汇总 | 判断 fix ROI、避免重复 |
| `remaining_gaps` | diagnosis 中 STRUCTURAL 标记 | 识别架构限制 |
| `next_optimization_target` | diagnosis fix_category + 文件路径 | 精确定位下一步改什么 |

优化目标映射（MetricRCA 特化）：

| 配置层 | MetricRCA 优化对象 |
|--------|-------------------|
| Prompt | `agent/prompts/rca_system.md`、tool descriptions |
| Tool | `agent/tools/*.py` 参数和阈值 |
| Policy | `plan_compiler.py` drilldown 策略、`contribution_set_builder.py` merge 规则 |
| Pipeline | evidence chain 拓扑、anomaly detection → RCA 的触发逻辑 |

### Layer 2: Sub-agent GRPO Reward Rules

#### Signal A (Task Trajectory)

Reward = 1.0 当且仅当所有以下 gate 通过：

```
intent_ok = 1
anomaly_ok = 1
dominant_top1_ok = 1 (单因) 或 top3_contains_all_major_causes = 1 (多因)
evidence_coverage = 1.0
sql_safe = 1
reflection_repair_ok = 1
report_traceable_ok = 1
memory_pollution_ok = 1
```

多因 case 额外要求：
```
weighted_explanation_coverage >= 0.85
root_cause_set_recall >= 0.85
```

否则 reward = 0.0（binary judge, 不做 partial credit）。

详见 `docs/final-design/07-grpo-eval-dataset.md`。

**训练目标**: MetricRCA 多智能体中的 planning agent、attribution agent 等子模型。

#### Signal B (Prediction Trajectory)

- `prediction_correct`: prediction 的 top1_ok/anomaly_ok 是否和 actual 一致
- `reasoning_quality`: 人工/LLM 评估 reasoning 的 specificity (0.0-1.0)

**训练目标**: planning agent 的系统行为预测能力（meta-reasoning）。

### Layer 3: Coding Model GRPO Reward Rules

#### Signal C (Diagnosis Trajectory)

- `fix_effective`: fix 后该 case 是否从 fail → pass
- `fix_minimal`: fix 后无其他 case 从 pass → fail

**训练目标**: 未来自训练编码模型的 diagnosis → fix 映射能力。

### 当前阶段策略

```
Layer 1: ACTIVE — 每轮 PTV 内循环直接消费
Layer 2: RECORD — Signal A/B 记录，积累 cycle 后启用训练
Layer 3: RECORD — Signal C 记录，编码模型训练启动后消费
```

Layer 2/3 启用后的 Signal 混合策略：A:B:C = 0.6:0.25:0.15（待实验验证后调整）。

## 7. Controller Decision Rules (MetricRCA 特化)

通用 RULE-C 规则见 `docs/ptv/02-workflow.md`。以下是 MetricRCA 的特化约束。

### Fix Category 优先级矩阵

当 diagnosis 同时包含多种 fix_category 时，按以下优先级选择：

```
优先级从高到低:
  FIX-ENUM  → 缺失枚举/policy 是基础阻塞，必须最先解决
  FIX-D     → discovery 缺口导致正确候选不在集合中，ranking 无用
  FIX-INJ   → 注入数据不足导致信号消失，下游判断无据可依
  FIX-M     → merge 逻辑影响多因 case 的候选集组合
  FIX-A     → attribution/ranking 只在候选集正确时有意义
  FIX-P     → prompt 调整是最轻量的干预
  FIX-T     → tool 变更影响范围大，谨慎选择
  FIX-B     → baseline 问题通常是孤立的
  FIX-S     → scorer 变更影响评估标准，需要仔细验证
  FIX-G     → gate 调整是最后手段
```

**关键约束**：如果存在 FIX-D 类型的 failing case，不允许跳过 FIX-D
直接做 FIX-A，除非 FIX-D 的所有 target case 已被标记为 STRUCTURAL。

### MetricRCA 常见自锁模式

以下模式已在实际 PTV cycle 中观察到，Controller 必须主动避免：

| 模式 | 症状 | 正确处理 |
|------|------|---------|
| ranking 乒乓 | FIX-A round N 提升 A 候选，round N+1 抑制 B 候选，指标来回波动 | 停止 FIX-A，检查是否是 FIX-D 缺失导致候选集不正确 |
| interaction 假阳性循环 | 反复在 ranking.py 添加 interaction 抑制/提升逻辑 | 问题通常在 plan_compiler 的 discovery 阶段：应该是 FIX-D 修改 drilldown 策略 |
| stockout/campaign 排序震荡 | stockout 和 campaign 在 top1 位置反复切换 | 检查 contribution_set_builder 的 merge 权重（FIX-M），而非 ranking 排序 |
| deferred FIX-D 死锁 | FIX-D 被连续 defer，因为 Controller 总是优先 "先稳定 ranking" | RULE-C2 强制提升；ranking 无法稳定是因为候选集本身不正确 |

### optimization_summary.json 必填字段 (MetricRCA)

除通用 Layer 1 字段外，MetricRCA 要求以下字段：

```json
{
  "controller_rules_applied": {
    "rule_c1_blocked_categories": [],
    "rule_c2_promoted": null,
    "rule_c3_discovery_priority": false,
    "rule_c4_revert_assessment": null,
    "rule_c5_streak_counts": {"FIX-A": 1}
  },
  "stall_analysis_from_analyst": { /* copied from ptv_trajectory */ }
}
```

## 8. Tooling Implementation

| 通用协议概念 | MetricRCA 实现 |
|-------------|---------------|
| Prediction schema | `metric_rca/evals/prediction.py` — `AspectPrediction` dataclass |
| Prediction validator | `python -m metric_rca.evals.prediction` CLI |
| Gap analyzer | `metric_rca/evals/gap_analyzer.py` — `analyze_gaps()` |
| GRPO export | `metric_rca/evals/grpo_dataset.py` |
| Scorer | `metric_rca/evals/scorer.py` |
| Eval runner | `metric_rca/evals/runner.py` |
