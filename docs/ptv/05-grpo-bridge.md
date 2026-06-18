# GRPO Bridge

## PTV → GRPO 映射

PTV 的每一轮产生的数据自然对应 GRPO 训练所需的三元组：

```
(trajectory, reward, reasoning_quality)
```

本文档定义从 PTV 产物到 GRPO 训练数据的通用转换规则。
项目特定的 reward 计算规则在 `bindings/` 中定义。

## PTV Trajectory Record

每轮 PTV 结束后写入 `ptv_trajectory.jsonl`，格式：

```json
{
  "ptv_round": 3,
  "eval_id": "eval-c3",
  "timestamp": "2026-06-18T10:30:00Z",
  "predictions_hash": "sha256:abc123...",
  "diagnosis_hash": "sha256:def456...",
  "fix_category": "FIX-ENUM",
  "fix_commit": "9726ee8",
  "metrics_before": {
    "accuracy": 0.86,
    "design_flaw_count": 3,
    "complexity_gap_count": 4
  },
  "metrics_after": {
    "accuracy": 0.91,
    "design_flaw_count": 1,
    "complexity_gap_count": 2
  },
  "prediction_accuracy": 0.89,
  "cases_fixed": ["IX01", "IX02", "IX03", "IX04"],
  "cases_regressed": [],
  "total_cases": 44,
  "exit_condition_met": false
}
```

## 从 PTV 产物到 GRPO 的三种信号

### Signal A: Task Trajectory (项目 eval 原生产出)

即项目 eval 的标准产物——agent 执行的完整 trace + 最终答案 + reward。
这是 GRPO 的基础训练数据，PTV 不改变它。

对应项目的 `grpo_dataset.py` 导出。

### Signal B: Prediction Trajectory (PTV 独有)

将每个 case 的预测本身作为一条训练轨迹：

```json
{
  "trajectory_type": "ptv_prediction",
  "case_id": "MC03",
  "input": {
    "system_state": "code snapshot hash",
    "case_description": "multi-cause GMV drop with channel+category interaction"
  },
  "output": {
    "prediction": {"top1_ok": false, "root_cause_type": null},
    "reasoning": "RootCauseType enum missing interaction_channel_category...",
    "risks": ["..."]
  },
  "reward": {
    "prediction_correct": true,
    "reasoning_quality": 0.9
  }
}
```

**Reward 规则：**
- `prediction_correct = (prediction matches actual outcome)`
- `reasoning_quality` = 项目绑定层定义的评分函数

Signal B 的核心价值：模型学习「如何预测系统行为」，而不仅仅是
「什么是正确答案」。这是系统理解能力的直接训练信号。

### Signal C: Diagnosis Trajectory (PTV 独有)

将诊断行为本身作为训练数据：

```json
{
  "trajectory_type": "ptv_diagnosis",
  "case_id": "IX01",
  "input": {
    "gap": {"divergence": "complexity_gap", "predicted": {...}, "actual": {...}},
    "code_context": "relevant code snapshot"
  },
  "output": {
    "diagnosis": "complexity_gap",
    "fix_category": "FIX-ENUM",
    "root_cause_analysis": "...",
    "proposed_fix": {...}
  },
  "reward": {
    "fix_effective": true,
    "fix_minimal": true
  }
}
```

**Reward 规则：**
- `fix_effective = (case passed after fix implementation)`
- `fix_minimal = (no regressions in linked cases)`

Signal C 的核心价值：模型学习「如何诊断和修复系统问题」。

## 三种信号的关系

```
┌────────────────────────────────────────────┐
│ Signal A: Task Trajectory                  │
│ "给定 prompt，agent 能否正确回答？"          │
│ → 训练 agent 的 task 执行能力               │
│                                            │
│ Signal B: Prediction Trajectory            │
│ "给定 system state，能否预测 agent 行为？"   │
│ → 训练 system reasoning 能力               │
│                                            │
│ Signal C: Diagnosis Trajectory             │
│ "给定 failure，能否正确诊断并修复？"          │
│ → 训练 debugging + fix 能力                │
└────────────────────────────────────────────┘
```

在 GRPO 训练中，三种信号可以：
1. **分别训练** — 每种信号训练不同的能力
2. **混合训练** — 按比例混合为一个统一的 reward model
3. **课程训练** — 先 A，再 A+B，最后 A+B+C

混合比例和训练策略在项目绑定层配置。

## 项目绑定层需要定义的接口

每个项目的 `bindings/<project>.md` 必须定义：

1. **reward 计算函数** — 从 eval 产物计算 Signal A 的 reward
2. **reasoning_quality 评分** — 从 prediction reasoning 评估质量
3. **fix_effective 判定** — 怎样算 fix 成功
4. **Signal 混合策略** — A:B:C 的比例或课程顺序
5. **trajectory 导出命令** — 如何从 eval_out 生成 GRPO dataset
