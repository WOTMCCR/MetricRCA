# PTV Data Bridge — 三层消费者

## PTV 产物的三层消费模型

PTV 的每一轮产生的数据同时服务三个不同层次的消费者。
它们不互斥，而是递进关系——从最即时（无训练）到最长期（权重更新）：

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: Agent System Optimization Context     ← 即时消费      │
│   消费者: Codex controller                                     │
│   作用: PTV 产物 → 结构化总结 → Codex 的 working memory         │
│   优化目标: 多智能体系统的配置层（prompt / tool / policy）       │
│   无需训练: 数据直接作为上下文注入                               │
│                                                                 │
│ Layer 2: Sub-agent GRPO                        ← 中期消费      │
│   消费者: 多智能体中的特定子模型（如 planning agent）            │
│   作用: task trajectory → 训练数据集 → 子模型权重更新           │
│   优化目标: 多智能体中具体 agent 的模型能力                     │
│                                                                 │
│ Layer 3: Coding Model GRPO                     ← 长期消费      │
│   消费者: 未来自训练的编码模型                                   │
│   作用: diagnosis + fix trajectory → 训练数据集 → 编码模型权重  │
│   优化目标: 代码诊断与修复能力                                  │
└─────────────────────────────────────────────────────────────────┘
```

三层共享同一套 PTV 产物，但消费方式完全不同。

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

---

## Layer 1: Agent System Optimization Context

这是 PTV 数据的**首要和即时消费场景**。

### 核心机制

Codex 作为多智能体系统的 controller，在每一轮 PTV 后从产物中提取
结构化总结，作为下一轮优化迭代的 working memory。这个过程不涉及
任何模型训练——"学习" 发生在 agent 系统的**可编程配置层**，而不是
模型权重中。

```
PTV round N 产物                    Codex controller 消费方式
─────────────────                   ─────────────────────────
gap_report.json         →  哪些场景类型系统性失败？模式是什么？
diagnosis.jsonl         →  失败的 root cause 在哪一层？什么类型的 fix 有效？
prediction_diff.json    →  上一轮 fix 是否改变了预测？偏差在哪里校正了？
ptv_trajectory.jsonl    →  accuracy 趋势、regression 检测、fix ROI
```

### 消费产物：Optimization Summary

每轮 PTV 后，Codex 应产出一份 optimization summary 供下一轮使用：

```json
{
  "summary_type": "agent_optimization_context",
  "ptv_round": 3,
  "failure_patterns": [
    {
      "pattern": "interaction cases (IX01-04) all fail at discovery layer",
      "affected_cases": ["IX01", "IX02", "IX03", "IX04"],
      "root_layer": "plan_compiler drilldown logic",
      "fix_category": "FIX-D",
      "confidence": "high — all 4 cases share identical failure mode"
    }
  ],
  "effective_fixes_history": [
    {"round": 1, "fix": "FIX-ENUM", "cases_fixed": 4, "regressions": 0},
    {"round": 2, "fix": "FIX-INJ", "cases_fixed": 2, "regressions": 0}
  ],
  "remaining_gaps": [
    {"family": "interaction", "blocker": "overall-first architecture", "fix_type": "STRUCTURAL"}
  ],
  "next_optimization_target": {
    "layer": "prompt",
    "files": ["agent/prompts/rca_system.md"],
    "rationale": "residual cases need explicit residual-chase instruction"
  }
}
```

### 优化目标举例

| 配置层 | 具体优化对象 | PTV 数据如何驱动 |
|--------|------------|-----------------|
| Prompt | agent system message, tool descriptions | diagnosis 显示 agent 不尝试某条推理路径 → 改 prompt |
| Tool | tool 参数、阈值、选择逻辑 | gap_report 显示 evidence chain 在某步断裂 → 改 tool config |
| Policy | drilldown 策略、merge 规则、ranking 权重 | prediction_diff 显示 fix 后 ranking 偏移 → 调 policy |
| Pipeline | evidence chain 顺序、并行策略 | STRUCTURAL 诊断 → 重新设计 pipeline 阶段 |

### 与 PTV 循环的关系

Layer 1 是 PTV 循环的**内循环消费者**——它在每一轮内部驱动 fix 决策。
Layer 2 和 3 是**外循环消费者**——它们在多轮积累后批量消费。

```
PTV round 1 → Layer 1 消费 → fix 决策 → code change
PTV round 2 → Layer 1 消费 → fix 决策 → code change
...
PTV cycle 完成 → 所有 round 的产物 → Layer 2/3 批量导出为训练数据
```

---

## Layer 2: Sub-agent GRPO Training

多智能体系统中的特定子模型（如 planning agent、attribution agent）
可以用 PTV 产物作为 GRPO 训练数据来强化自身能力。

### Signal A: Task Trajectory（子 agent 任务执行能力）

即项目 eval 的标准产物——agent 执行的完整 trace + 最终答案 + reward。
这是子 agent GRPO 的基础训练数据。

```json
{
  "trajectory_type": "task",
  "case_id": "MC01",
  "agent_id": "planning_agent",
  "input": {"question": "Why did overall GMV fall on May 29?", "context": "..."},
  "trace": ["detect_anomaly → drilldown_channel → drilldown_category → rank"],
  "output": {"root_cause_type": "campaign_traffic_drop", "dimension": "channel", "element": "paid_ads"},
  "reward": 1.0
}
```

**训练目标**: 子 agent 在给定 prompt 下能否执行正确的推理路径。

对应项目的 `grpo_dataset.py` 导出。

### Signal B: Prediction Trajectory（系统建模能力）

将每个 case 的预测本身作为一条训练轨迹。这个信号的价值在于教模型
理解系统的能力边界——不是 "什么是正确答案"，而是 "当前系统在这个
输入下会产生什么行为"。

```json
{
  "trajectory_type": "ptv_prediction",
  "case_id": "MC03",
  "input": {
    "system_state": "code snapshot hash",
    "case_description": "multi-cause CVR drop with social+organic conversion suppression"
  },
  "output": {
    "prediction": {"top1_ok": false, "root_cause_type": null},
    "reasoning": "CVR suppressor only fires on MULTI_CAUSE_CVR_DATE, but drilldown won't reach channel-level CVR decomposition...",
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

**训练目标**: 模型学习对多智能体系统行为的预测能力。
适用于训练 planning agent 或 meta-reasoning agent。

---

## Layer 3: Coding Model GRPO Training

PTV 的 diagnosis → fix 轨迹天然适合训练编码模型：给定一个系统缺陷的
结构化描述，模型能否产出正确的最小修复。

### Signal C: Diagnosis Trajectory（代码诊断与修复能力）

```json
{
  "trajectory_type": "ptv_diagnosis",
  "case_id": "IX01",
  "input": {
    "gap": {"divergence": "complexity_gap", "predicted": {"top1_ok": false}, "actual": {"top1_ok": false}},
    "code_context": "relevant code snapshot"
  },
  "output": {
    "diagnosis": "complexity_gap",
    "fix_category": "FIX-ENUM",
    "root_cause_analysis": "RootCauseType enum missing interaction_channel_category...",
    "proposed_fix": {"file": "metric_rca/domain/enums.py", "change": "add enum member"}
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

**训练目标**: 编码模型学习从结构化缺陷描述到最小代码修复的映射。

### Coding Model 训练数据的额外价值

PTV 产物比普通 bug-fix 训练数据更丰富，因为它包含：

| 维度 | 普通 bug-fix 数据 | PTV diagnosis 数据 |
|------|-------------------|-------------------|
| 缺陷描述 | issue title + description | 结构化 gap（prediction vs actual） |
| 修复范围 | commit diff | fix_category + 精确文件列表 |
| 验证信号 | CI 通过/失败 | case-level pass/fail + regression 检测 |
| 推理过程 | 无 | prediction reasoning + diagnosis analysis |
| 因果链 | 无 | 跨 round 的 fix → effect 追踪 |

---

## 三种信号 × 三层消费者

```
                    Layer 1           Layer 2            Layer 3
                    Context           Sub-agent GRPO     Coding Model GRPO
                    ─────────         ─────────────      ──────────────────
Signal A (Task)     pattern 提取       主要训练信号        参考
Signal B (Predict)  预测校准           planning agent     参考
Signal C (Diagnose) fix 决策驱动       参考               主要训练信号
Trajectory Record   趋势 + ROI 分析   epoch 边界标记      epoch 边界标记
```

Layer 1 **全部消费**，但消费方式是提取模式和驱动决策，不是梯度更新。
Layer 2 主要消费 Signal A + B，用于强化子 agent 的推理能力。
Layer 3 主要消费 Signal C，用于训练诊断 → 修复的映射。

## GRPO 训练策略（Layer 2 & 3）

在 GRPO 训练中，三种信号可以：
1. **分别训练** — 每种信号训练不同的模型/能力
2. **混合训练** — 按比例混合为一个统一的 reward model
3. **课程训练** — 先 A，再 A+B，最后 A+B+C

混合比例和训练策略在项目绑定层配置。

当前阶段（Phase C）：Layer 1 active，Layer 2/3 仅记录。
数据格式已为三层消费就绪，Layer 2/3 在积累足够 cycle 后启用。

## 项目绑定层需要定义的接口

每个项目的 `bindings/<project>.md` 必须定义：

1. **optimization summary schema** — Layer 1 的总结格式和内容要求
2. **reward 计算函数** — 从 eval 产物计算 Signal A 的 reward
3. **reasoning_quality 评分** — 从 prediction reasoning 评估质量
4. **fix_effective 判定** — 怎样算 fix 成功
5. **Signal 混合策略** — A:B:C 的比例或课程顺序（Layer 2/3）
6. **trajectory 导出命令** — 如何从 eval_out 生成 GRPO dataset
