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

## 6. GRPO Reward Rules

### Signal A (Task Trajectory)

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

### Signal B (Prediction Trajectory)

- `prediction_correct`: prediction 的 top1_ok/anomaly_ok 是否和 actual 一致
- `reasoning_quality`: 人工/LLM 评估 reasoning 的 specificity (0.0-1.0)

### Signal C (Diagnosis Trajectory)

- `fix_effective`: fix 后该 case 是否从 fail → pass
- `fix_minimal`: fix 后无其他 case 从 pass → fail

### Signal 混合策略

当前阶段（Phase C）：A only，B 和 C 记录但不用于训练。
未来 GRPO 训练启动后：A:B:C = 0.6:0.25:0.15（待实验验证后调整）。

## 7. Tooling Implementation

| 通用协议概念 | MetricRCA 实现 |
|-------------|---------------|
| Prediction schema | `metric_rca/evals/prediction.py` — `AspectPrediction` dataclass |
| Prediction validator | `python -m metric_rca.evals.prediction` CLI |
| Gap analyzer | `metric_rca/evals/gap_analyzer.py` — `analyze_gaps()` |
| GRPO export | `metric_rca/evals/grpo_dataset.py` |
| Scorer | `metric_rca/evals/scorer.py` |
| Eval runner | `metric_rca/evals/runner.py` |
