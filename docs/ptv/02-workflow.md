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

### 推荐模式：Parallel Dispatch

PTV 的核心设计是让 Codex 的 Controller 通过 subagent 分发来并行化
耗时步骤。每个 subagent 有独立的职责和输出。

```
┌─────────────────────────────────────────────────────────────┐
│ CONTROLLER (Codex main thread)                              │
│                                                             │
│ 管理 PTV cycle: 创建 round 目录, 调度 subagent, 做 fix 决策  │
│ Layer 1 消费者: 从 PTV 产物中提取 optimization summary,      │
│ 作为下一轮多智能体系统优化的 working memory                   │
│                                                             │
│ for round in 1..MAX_ROUNDS:                                 │
│                                                             │
│   ┌─ PARALLEL DISPATCH ─────────────────────────────┐       │
│   │                                                  │       │
│   │  ┌─ PREDICTION AGENT ────────────────────┐       │       │
│   │  │ 职责:                                 │       │       │
│   │  │  - 读当前代码状态                      │       │       │
│   │  │  - 为每个 eval case 写 prediction     │       │       │
│   │  │  - round > 1 时，读上一轮 gap_report,  │       │       │
│   │  │    生成 prediction_diff.json           │       │       │
│   │  │  - 验证 predictions 通过 validator     │       │       │
│   │  │ 输入: round, previous gap_report       │       │       │
│   │  │ 输出: predictions.jsonl,               │       │       │
│   │  │       prediction_diff.json             │       │       │
│   │  └───────────────────────────────────────┘       │       │
│   │                                                  │       │
│   │  ┌─ EVAL AGENT ─────────────────────────┐        │       │
│   │  │ 职责:                                 │       │       │
│   │  │  - 运行 eval harness (make eval-stream)│      │       │
│   │  │  - 收集 per-case artifacts             │       │       │
│   │  │ 输入: eval command, output directory    │       │       │
│   │  │ 输出: eval-result.json, per-case/*.json│       │       │
│   │  └───────────────────────────────────────┘       │       │
│   │                                                  │       │
│   └──────────────────────────────────────────────────┘       │
│                      ↓ BOTH complete (barrier)               │
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
│   Controller (Layer 1 consumption):                         │
│   - 读 diagnosis + trajectory + gap_report                  │
│   - 提取 optimization summary (见 05-grpo-bridge.md Layer 1)│
│   - 判断: exit / escalate / fix                             │
│   - 如果 fix: 基于 summary 决定优化哪一层配置               │
│     (prompt / tool / policy / pipeline)                     │
│   - 实现修复, 跑 tests, commit                              │
│   - 下一轮 round += 1                                       │
└─────────────────────────────────────────────────────────────┘
```

### 并行时序

```
Time →
─────────────────────────────────────────────────────────
Controller:  [create round dir]
Prediction:  [read code → write predictions ····]
Eval:        [run eval ·························]
             ← 两者并行，无依赖 →
                                                ↓ barrier
Analyst:     ·································  [analyze → diagnose]
                                                                    ↓
Controller:  ·································  ·················  [summary → fix → commit]
─────────────────────────────────────────────────────────
```

**并行安全性**：Prediction Agent 和 Eval Agent 之间没有运行时依赖。
Eval 的 `require_predictions` 是可选 gate，不影响 case selection 或
执行逻辑。真正需要两者都完成的是 Analyst Agent（它比较 predictions
vs actuals 来生成 gap_report）。

Analyst Agent 和 Eval Agent 之间是串行的（Analyst 需要 eval 结果）。
跨 round 可以有进一步的流水线重叠——Controller commit 后可以立即
dispatch 下一轮的 Prediction Agent + Eval Agent。

### Fallback：Sequential Mode

如果 Codex 运行时不支持 subagent，Controller 自己按顺序执行所有步骤。
**约束不变**：DIAGNOSE 步骤不可跳过，Controller Decision Rules 同样适用。

## Controller Decision Rules (RULE-C)

Controller 在每轮 STEP 5/6 之间做 fix 决策时，**必须遵守**以下规则。
这些规则的目的是防止 Controller 陷入「重复同一类 fix 但不进步」的自锁循环。

### RULE-C1: No Repeat After Regression

如果 round N 应用了 fix_category X，并且 round N 的任一聚合指标
（top1_rate, top3_rate, multi_cause_top3_rate）相比 round N-1 **回退**，
则 round N+1 **禁止**再选 X。

Controller 必须在 optimization_summary.json 中声明：
```json
"rule_c1_blocked_categories": ["FIX-A"],
"rule_c1_reason": "round-02 applied FIX-A, top1 regressed 0.826→0.783"
```

### RULE-C2: Deferred Target Promotion

如果某个 fix_category 连续出现在 `deferred_targets` 中 **≥2 轮**，
下一轮 **必须** 选择该 category 作为主修复目标。不能无限期 defer。

触发时，Controller 声明：
```json
"rule_c2_promoted": "FIX-D",
"rule_c2_reason": "deferred in round-01 and round-02, mandatory promotion"
```

### RULE-C3: Discovery Before Attribution

当 diagnosis 同时包含 FIX-D（discovery/plan_compiler）和 FIX-A
（attribution/ranking）问题时，**FIX-D 优先**。

原因：ranking 修复无法作用于从未被发现的候选。如果正确候选不在
contribution set 中，排序修复只是在错误的候选集合中调换位置。

例外：如果 FIX-D 的所有 target case 都已被标记为 STRUCTURAL，
可以跳过 FIX-D 转回 FIX-A。

### RULE-C4: Regression Revert Gate

如果 round N 的 **≥2 个聚合指标** 相比 round N-1 回退，Controller
必须在 optimization_summary.json 中写入 `revert_assessment`：

```json
"revert_assessment": {
  "regressed_metrics": ["top1_rate", "multi_cause_top3_rate"],
  "revert_decision": "revert | keep",
  "justification": "commit 33e50a7 introduced RS01 regression via over-broad
    interaction demotion; reverting restores round-01 baseline before switching
    to FIX-D"
}
```

**If revert**: `git revert <commit>`, 记录到 fix_commit.txt，
下一轮基于 revert 后的代码状态继续。
**If keep**: 必须说明为什么保留回退的 commit 比 revert 更好。

### RULE-C5: Max Consecutive Same Category

同一个 fix_category 不能连续应用超过 **2 轮**。第 3 轮必须切换。

如果 Controller 认为该 category 仍然需要，必须先完成至少 1 轮
其他 category 的修复后再回到该 category。

### 规则优先级

当多条规则冲突时，按以下优先级解决：
```
RULE-C1 (回退后禁止) > RULE-C2 (defer 提升) > RULE-C3 (discovery 优先)
> RULE-C5 (连续上限) > Controller 自主判断
```

RULE-C4 (revert gate) 独立于其他规则，每轮都必须评估。

### Subagent Prompt 模板

每个 subagent 的 dispatch prompt 应包含以下内容。**粗体**部分是
相比基础指令新增的约束，用于防止自锁循环。

```text
── 并行 Phase（同时 dispatch）──────────────────────

PREDICTION AGENT prompt:
  "Read docs/ptv/03-prediction-protocol.md and bindings/metricrca.md.
   Read the current code state for [case families].
   Write predictions to eval_out/ptv/cycle-{id}/round-{N}/predictions.jsonl.
   [if round > 1] Read eval_out/ptv/cycle-{id}/round-{N-1}/gap_report.json
   and write prediction_diff.json.
   Validate: python -m metric_rca.evals.prediction <output path> must exit 0.

   PREDICTION QUALITY RULES:
   - For each case, reasoning MUST reference specific code paths (file:line).
   - If a case failed in the previous round with the SAME fix_category as the
     round before that, flag it in risks[] as 'stall_risk: {category} applied
     {N} consecutive rounds without resolving this case'.
   - Prediction confidence should decrease for cases that have been failing
     across consecutive rounds of the same fix_category."

EVAL AGENT prompt:
  "Run: make eval-stream EVAL_ID=ptv-cycle-{id}-round-{N}
   Copy results to eval_out/ptv/cycle-{id}/round-{N}/eval-result.json
   Copy per-case artifacts to eval_out/ptv/cycle-{id}/round-{N}/per-case/
   Stream log to eval_out/ptv/cycle-{id}/round-{N}/eval-stream.log"

── 串行 Phase（等两者都完成后 dispatch）────────────

PTV ANALYST AGENT prompt:
  "Read docs/ptv/04-diagnosis-protocol.md and bindings/metricrca.md.
   Read predictions from round-{N}/predictions.jsonl.
   Read eval results from round-{N}/eval-result.json.
   [if round > 1] Read ALL prior round optimization_summary.json files.
   Run: make eval-gaps EVAL_ID=ptv-cycle-{id}-round-{N}
   Write diagnosis.jsonl for every non-correct gap.
   Write ptv_trajectory.jsonl with prediction accuracy.
   Use fix taxonomy from bindings/metricrca.md.

   ANALYST LOOP DETECTION (mandatory for round > 1):
   1. Count how many consecutive rounds each fix_category has been applied.
      Include this as 'category_streak' in ptv_trajectory.jsonl.
   2. For each failing case, check if it has failed with the SAME top-1
      fix_category in the previous round. If so, mark it as stall_risk=high
      in diagnosis.jsonl.
   3. Write a 'stall_analysis' section in ptv_trajectory.jsonl:
      {
        'stall_analysis': {
          'current_streak': {'FIX-A': 2},
          'deferred_streak': {'FIX-D': 2, 'FIX-M': 1},
          'rule_c1_triggered': true/false,
          'rule_c2_triggered': true/false,
          'rule_c5_triggered': true/false,
          'recommended_next_category': 'FIX-D',
          'recommendation_reason': 'FIX-A applied 2 rounds with regression;
            FIX-D deferred 2 rounds (RULE-C2 mandatory promotion);
            discovery gaps block 2 cases that ranking cannot fix (RULE-C3)'
        }
      }
   4. The recommended_next_category MUST respect RULE-C1 through C5.
      If the Analyst recommends a category that violates a rule,
      explain why no compliant alternative exists (this triggers escalation)."

── Controller Phase（Analyst 完成后）───────────────

CONTROLLER decision procedure:
  1. Read diagnosis.jsonl + ptv_trajectory.jsonl (especially stall_analysis)
  2. Read ALL prior round optimization_summary.json files
  3. Evaluate RULE-C4 (regression revert gate):
     - If ≥2 aggregate metrics regressed, write revert_assessment
     - If reverting, execute revert before proceeding
  4. Determine fix_category:
     a. Start with Analyst's recommended_next_category
     b. Validate against RULE-C1 (blocked categories from regression)
     c. Validate against RULE-C2 (mandatory promotions from deferral)
     d. Validate against RULE-C3 (discovery priority)
     e. Validate against RULE-C5 (consecutive cap)
     f. If all rules are satisfied, select. If not, escalate.
  5. Write optimization_summary.json with:
     - rule_c1_blocked_categories (if any)
     - rule_c2_promoted (if any)
     - revert_assessment (if RULE-C4 triggered)
     - selected fix_category + justification referencing rules
  6. Implement fix, run tests, commit, write fix_commit.txt
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
