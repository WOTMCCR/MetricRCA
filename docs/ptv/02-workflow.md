# PTV Workflow

## Artifact Isolation

每一轮 PTV 的产物必须隔离到独立目录，便于审阅、跨轮 diff、训练数据提取。

### 目录结构

```
eval_out/
  ptv/
    cycle-{YYYYMMDD-HHMM}/          ← 一个完整 PTV cycle（round 1 到退出/升级）
      meta.json                      ← cycle 元信息
      round-01/
        predictions.jsonl            ← STEP 1 输出
        eval-result.json             ← STEP 2 输出（eval harness 产物）
        per-case/                    ← STEP 2 输出（每个 case 的详细 artifacts）
          MC01.json
          MC02.json
          ...
        gap_report.json              ← STEP 3 输出
        diagnosis.jsonl              ← STEP 4 输出
        ptv_trajectory.jsonl         ← STEP 7 输出
        fix_commit.txt               ← STEP 6 输出（commit hash + message）
      round-02/
        predictions.jsonl
        prediction_diff.json         ← 和 round-01 的 prediction 差异
        ...
      round-N/
        ...
      escalation.json                ← 如果 cycle 以升级结束
      summary.json                   ← cycle 完成后的汇总
```

### meta.json

```json
{
  "cycle_id": "cycle-20260618-1030",
  "branch": "codex/c-complex-causal",
  "base_commit": "9726ee8",
  "started_at": "2026-06-18T10:30:00Z",
  "total_cases": 44,
  "max_rounds": 6,
  "project_binding": "metricrca",
  "status": "in_progress | completed | escalated"
}
```

### prediction_diff.json (round > 1)

```json
{
  "from_round": 1,
  "to_round": 2,
  "changes": [
    {
      "case_id": "IX01",
      "field": "prediction.top1_ok",
      "old": false,
      "new": true,
      "reason": "FIX-ENUM added interaction_channel_category to RootCauseType"
    }
  ],
  "unchanged_count": 40,
  "changed_count": 4
}
```

### summary.json (cycle 完成后)

```json
{
  "cycle_id": "cycle-20260618-1030",
  "total_rounds": 3,
  "exit_reason": "all_gates_passed | escalation | max_rounds",
  "final_accuracy": 0.95,
  "prediction_accuracy_trend": [0.86, 0.91, 0.95],
  "fixes_applied": [
    {"round": 1, "category": "FIX-ENUM", "commit": "abc123", "cases_fixed": 4},
    {"round": 2, "category": "FIX-INJ", "commit": "def456", "cases_fixed": 2}
  ],
  "grpo_signals": {
    "signal_a_trajectories": 44,
    "signal_b_predictions": 132,
    "signal_c_diagnoses": 14
  }
}
```

## Loop Structure

```
round = 1

while round <= MAX_ROUNDS:

    ┌─ STEP 1: PREDICT ─────────────────────────────────┐
    │ Write predictions for ALL eval cases.              │
    │ Each prediction must reference current code state. │
    │ For round > 1, also write prediction_diff.json.    │
    │ Output: ptv/cycle-{id}/round-{N}/predictions.jsonl │
    │ Validate: prediction validator must exit 0         │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 2: EXECUTE ─────────────────────────────────┐
    │ Run the eval harness against the current system.   │
    │ Output: eval results + per-case artifacts          │
    │ → ptv/cycle-{id}/round-{N}/eval-result.json       │
    │ → ptv/cycle-{id}/round-{N}/per-case/*.json        │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 3: VERIFY ──────────────────────────────────┐
    │ Run the gap analyzer: predictions vs actuals.      │
    │ Output: ptv/cycle-{id}/round-{N}/gap_report.json  │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 4: DIAGNOSE (MANDATORY) ────────────────────┐
    │ For every divergent case, write a structured       │
    │ diagnosis entry with:                              │
    │   - failure category (from project fix taxonomy)   │
    │   - root cause analysis                            │
    │   - proposed fix + files to change                 │
    │ Output: ptv/cycle-{id}/round-{N}/diagnosis.jsonl  │
    │                                                    │
    │ CANNOT SKIP. "Next iteration scope" without        │
    │ diagnosis is a protocol violation.                  │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 5: CHECK EXIT ──────────────────────────────┐
    │ If all project-defined gates pass:                  │
    │   Run ONE MORE TIME for confirmation.              │
    │   If confirmed → EXIT LOOP, write summary.json.   │
    │                                                    │
    │ If STRUCTURAL blockers exist and round > threshold:│
    │   → ESCALATE, write escalation.json               │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 6: FIX ────────────────────────────────────┐
    │ Implement the MINIMAL fix from diagnosis.          │
    │ ONE fix category per round (no batching).          │
    │ Run project tests after fix.                       │
    │ Commit with structured message.                    │
    │ Write commit hash to fix_commit.txt                │
    └───────────────────────────────────────────────────┘
                         │
    ┌─ STEP 7: RECORD TRAJECTORY ───────────────────────┐
    │ Write ptv_trajectory.jsonl with:                   │
    │   - round number, eval_id                          │
    │   - predictions hash, diagnosis hash               │
    │   - fix category, commit hash                      │
    │   - metrics before/after                           │
    │   - prediction accuracy                            │
    └───────────────────────────────────────────────────┘
                         │
    round += 1
    Go to STEP 1 with UPDATED predictions.
```

## Codex Subagent Dispatch Pattern

### 推荐模式：3-Agent Parallel

PTV 的核心设计是让 Codex 的 Controller 通过 subagent 分发来并行化
耗时步骤。每个 subagent 有独立的职责和输出。

```
┌─────────────────────────────────────────────────────────────┐
│ CONTROLLER (Codex main thread)                              │
│                                                             │
│ 管理 PTV cycle: 创建 round 目录, 调度 subagent, 做 fix 决策  │
│                                                             │
│ for round in 1..MAX_ROUNDS:                                 │
│                                                             │
│   ┌─ DISPATCH: PREDICTION AGENT ────────────────────┐       │
│   │ 职责:                                           │       │
│   │  - 读当前代码状态                                │       │
│   │  - 为每个 eval case 写 prediction               │       │
│   │  - round > 1 时，读上一轮 gap_report,            │       │
│   │    生成 prediction_diff.json                     │       │
│   │  - 验证 predictions 通过 validator               │       │
│   │ 输入: round number, previous gap_report (if any) │       │
│   │ 输出: predictions.jsonl, prediction_diff.json    │       │
│   └─────────────────────────────────────────────────┘       │
│                      ↓ predictions ready                    │
│   ┌─ DISPATCH: EVAL AGENT ─────────────────────────┐        │
│   │ 职责:                                           │       │
│   │  - 运行 eval harness (make eval-stream)         │       │
│   │  - 收集 per-case artifacts                      │       │
│   │ 输入: eval command, output directory             │       │
│   │ 输出: eval-result.json, per-case/*.json          │       │
│   └─────────────────────────────────────────────────┘       │
│                      ↓ eval complete                        │
│   ┌─ DISPATCH: PTV ANALYST AGENT ──────────────────┐        │
│   │ 职责:                                           │       │
│   │  - 运行 gap analyzer (make eval-gaps)           │       │
│   │  - 读 predictions + eval results + per-case      │       │
│   │  - 写 diagnosis.jsonl (每个 failing case)        │       │
│   │  - 写 ptv_trajectory.jsonl                       │       │
│   │  - 计算 prediction accuracy                      │       │
│   │ 输入: predictions.jsonl, eval-result.json        │       │
│   │ 输出: gap_report.json, diagnosis.jsonl,          │       │
│   │       ptv_trajectory.jsonl                       │       │
│   └─────────────────────────────────────────────────┘       │
│                      ↓ analysis complete                    │
│   Controller:                                               │
│   - 读 diagnosis + trajectory                               │
│   - 判断: exit / escalate / fix                             │
│   - 如果 fix: 实现修复, 跑 tests, commit                    │
│   - 下一轮 round += 1                                       │
└─────────────────────────────────────────────────────────────┘
```

### 并行时序

```
Time →
────────────────────────────────────────────────
Controller:  [create round dir]
Prediction:  [read code → write predictions  ]
                                    ↓
Eval:        ·····················  [run eval ····················]
Analyst:     ·····················  ···  [wait for eval → analyze → diagnose]
                                                                    ↓
Controller:  ·····················  ···  ·························  [read → fix → commit]
────────────────────────────────────────────────
```

注意 Prediction Agent 必须在 Eval Agent 之前完成（eval 需要 predictions
来决定运行哪些 case 或标记模式）。Eval Agent 和 Analyst Agent 之间是
串行的（Analyst 需要 eval 结果）。但 Prediction Agent 和上一轮的 fix
可以有重叠——Controller commit 后可以立即 dispatch 下一轮的 Prediction Agent。

### Fallback：Sequential Mode

如果 Codex 运行时不支持 subagent，Controller 自己按顺序执行所有步骤。
**约束不变**：DIAGNOSE 步骤不可跳过。

### Subagent Prompt 模板

每个 subagent 的 dispatch prompt 应包含：

```text
PREDICTION AGENT prompt:
  "Read docs/ptv/03-prediction-protocol.md and bindings/metricrca.md.
   Read the current code state for [case families].
   Write predictions to eval_out/ptv/cycle-{id}/round-{N}/predictions.jsonl.
   [if round > 1] Read eval_out/ptv/cycle-{id}/round-{N-1}/gap_report.json
   and write prediction_diff.json.
   Validate: python -m metric_rca.evals.prediction <output path> must exit 0."

EVAL AGENT prompt:
  "Run: make eval-stream EVAL_ID=ptv-cycle-{id}-round-{N}
   Copy results to eval_out/ptv/cycle-{id}/round-{N}/eval-result.json
   Copy per-case artifacts to eval_out/ptv/cycle-{id}/round-{N}/per-case/"

PTV ANALYST AGENT prompt:
  "Read docs/ptv/04-diagnosis-protocol.md and bindings/metricrca.md.
   Read predictions from round-{N}/predictions.jsonl.
   Read eval results from round-{N}/eval-result.json.
   Run: make eval-gaps EVAL_ID=ptv-cycle-{id}-round-{N}
   Write diagnosis.jsonl for every non-correct gap.
   Write ptv_trajectory.jsonl with prediction accuracy.
   Use fix taxonomy from bindings/metricrca.md."
```

## Round Commit Convention

每个包含代码修复的 PTV round 必须产生一个 commit：

```
fix(<phase>/ptv-{round}): {FIX-CATEGORY} {description}
```

示例：
```
fix(phase-c/ptv-1): FIX-ENUM add interaction_channel_category to RootCauseType
fix(phase-c/ptv-2): FIX-INJ strengthen cvr injection on MULTI_CAUSE_DATE
fix(phase-c/ptv-3): FIX-D add parallel drilldown for rate_family
```

## Escalation Protocol

升级触发条件：
1. Round 数超过项目定义的阈值（默认 6）
2. 剩余失败全部被诊断为 STRUCTURAL
3. 所有非 STRUCTURAL fix 已尝试过

升级输出写入 `ptv/cycle-{id}/escalation.json`：
- 失败 case 列表 + 跨所有 round 的诊断历史
- 需要的新能力描述
- 指向扩展设计文档的指针

## 什么触发新的 PTV Cycle

以下变化需要从 round 1 重新开始一个新 cycle（新的 `cycle-{id}` 目录）：

1. **Eval 数据变化** — 新 case、修改 ground truth、更新注入/seed 数据
2. **架构变化** — 新 tool、新 evidence type、新 pipeline 阶段
3. **Scoring 变化** — 新指标、修改 gate、新 reward 规则

同一个 cycle 内的 round 之间共享相同的 eval case set 和 scoring 规则。
