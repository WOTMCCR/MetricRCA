# Codex Dispatch — Phase C Sessions

## Pre-dispatch Checklist

```bash
# 1. Create branch
git checkout -b codex/c-complex-causal main

# 2. Verify baseline
make up
make seed SEED_PROFILE=regression
make test          # must pass
make eval-regression  # must show 28/28 green

# 3. Record baseline commit
git log --oneline -1 > eval_out/phase-c-baseline.txt
```

## Session 1 Prompt (Iterations 0-1)

```text
Read docs/iteration-prompts/12-phase-c-complex-causal-coverage.md
in full. Execute Iterations 0 and 1 only.

You are on branch codex/c-complex-causal. MySQL is running.
Run `make seed SEED_PROFILE=regression` before starting.

ITERATION 0: cleanup legacy cases.jsonl, create tests/legacy_migration/,
add root_causes column migration, create tests/test_e2e_smoke.py,
add make test-e2e target.

ITERATION 1: add new anomaly injection functions for multi-cause,
interaction, lagged, and weak-signal scenarios on new dates. Do NOT
touch existing injection behavior.

At each gate, run the validation commands and confirm they pass.
Commit after each iteration with prefix "feat(phase-c/iter-N):".
```

## Session 2 Prompt (Iterations 2-3)

```text
Read docs/iteration-prompts/12-phase-c-complex-causal-coverage.md
in full. Execute Iterations 2 and 3 only.

You are on branch codex/c-complex-causal with Iterations 0-1 complete.
Run `make seed SEED_PROFILE=regression` to pick up new ground truth.

ITERATION 2: add 16+ new eval cases (multi-cause, interaction, lagged,
weak-signal) with ground truth using root_causes JSON weights.

ITERATION 3: extend PlanCompiler for parallel drilldown chains,
build ContributionSetBuilder for cross-chain merge, and add Reflection
cross-chain consistency check.

CRITICAL: the original 28 cases MUST stay 28/28 green after every
change. Run make eval-regression to verify after each iteration.
Commit after each iteration.
```

## Session 3 Prompt (Iteration 4 — PTV Loop)

```text
Read the following documents in order:
  1. docs/ptv/README.md                    — PTV overview and directory layout
  2. docs/ptv/01-philosophy.md             — what PTV is and is not
  3. docs/ptv/02-workflow.md               — loop structure, artifact isolation,
     subagent dispatch, AND Controller Decision Rules (RULE-C section)
  4. docs/ptv/03-prediction-protocol.md    — prediction rules R1-R5
  5. docs/ptv/04-diagnosis-protocol.md     — diagnosis schema, fix taxonomy,
     stall detection, escalation triggers
  6. docs/ptv/06-enforcement.md            — anti-cheat detection (you will be audited)
  7. docs/ptv/bindings/metricrca.md        — MetricRCA-specific aspects, commands,
     exit conditions, Controller Decision Rules binding, fix priority matrix
  8. docs/iteration-prompts/12-phase-c-complex-causal-coverage.md (ITERATION 4 section only)

Execute Iteration 4: PTV optimization loop.

You are on branch codex/c-complex-causal with Iterations 0-3 and
review fixes complete. Run make seed && make test to verify baseline.

EXECUTION INSTRUCTIONS:

1. Create a PTV cycle directory:
     CYCLE_ID=$(date +%Y%m%d-%H%M)
     mkdir -p eval_out/ptv/cycle-${CYCLE_ID}
   Write meta.json with branch, base_commit, total_cases.

2. Use PARALLEL DISPATCH pattern from docs/ptv/02-workflow.md:
   For each PTV round:
     a. PARALLEL DISPATCH:
        - Prediction Agent → reads code + previous gap_report,
          writes predictions.jsonl to eval_out/ptv/cycle-{id}/round-{N}/
        - Eval Agent → runs make eval-stream, writes eval-result.json
          and per-case artifacts to same round directory
        (These two have NO dependency — dispatch simultaneously)
     b. BARRIER: wait for BOTH (a) to complete
     c. DISPATCH PTV Analyst Agent → runs gap analyzer, reads predictions +
        eval results, writes gap_report.json + diagnosis.jsonl +
        ptv_trajectory.jsonl to same round directory.
        ANALYST MUST include stall_analysis in ptv_trajectory.jsonl
        (see 04-diagnosis-protocol.md and 02-workflow.md Analyst prompt).
     d. Controller reads diagnosis + trajectory + stall_analysis,
        produces optimization_summary.json (Layer 1 — see 05-grpo-bridge.md).
        CONTROLLER MUST follow RULE-C1 through RULE-C5 (see 02-workflow.md).
     e. If fix: based on optimization_summary AND RULE-C constraints,
        choose fix_category. Write rule_c* fields to optimization_summary.
        Implement, test, commit, write fix_commit.txt.
     f. Increment round, go to step (a)

   If your runtime does not support subagents, run steps (a)-(c)
   sequentially but NEVER skip diagnosis, stall_analysis, or
   optimization_summary. Controller Decision Rules still apply.

3. CONTROLLER DECISION RULES (MANDATORY — read 02-workflow.md §RULE-C):
   - RULE-C1: If fix_category X caused metric regression, round N+1
     CANNOT use X. Write rule_c1_blocked_categories to summary.
   - RULE-C2: If a category has been deferred ≥2 rounds, it MUST be
     selected next. Cannot defer indefinitely.
   - RULE-C3: FIX-D (discovery) takes priority over FIX-A (ranking)
     when both are present. Ranking fixes are useless if correct
     candidates are never discovered.
   - RULE-C4: If ≥2 aggregate metrics regressed, write revert_assessment.
     Decide revert or keep with justification.
   - RULE-C5: Same fix_category cannot run >2 consecutive rounds.

   ANTI-STALL PROTOCOL: If the Analyst's stall_analysis shows
   category_streak ≥2 for any category, you MUST switch categories.
   The "stabilize X first before doing Y" rationale is INVALID after
   2 rounds of X without net improvement.

4. All PTV artifacts MUST go into eval_out/ptv/cycle-{id}/round-{N}/.
   Do NOT scatter artifacts across eval_out/{eval_id}/ directories.

5. After the session, your artifacts will be reviewed against the
   anti-cheat checklist in docs/ptv/06-enforcement.md. Specifically:
     - DETECT-1: predictions must NOT match ground truth
     - DETECT-2: predictions must differ between rounds
     - DETECT-3: every round must have a fix commit
     - DETECT-4: diagnosis.jsonl must exist for every round with failures
     - DETECT-5: reasoning must be case-specific, not templated
     - DETECT-6: optimization_summary must contain rule_c* fields (round > 1)
     - DETECT-7: same fix_category must not appear >2 consecutive rounds

6. EXIT when MetricRCA gates pass (see bindings/metricrca.md).
   ESCALATE after 6 rounds with STRUCTURAL diagnosis.
```

## Session 4 Prompt (Iteration 5 — Conditional Sandbox)

```text
Read docs/iteration-prompts/12-phase-c-complex-causal-coverage.md
AND docs/final-design/08-python-analyst-extension.md in full.
Execute Iteration 5: Python Analyst Sandbox.

This session runs ONLY if Session 3 triggered escalation.
You are on branch codex/c-complex-causal.

Implement in order:
  1. AnalysisFrame builder (read-only artifact extraction)
  2. Lag scan computation (cross-correlation across T-1 to T-7)
  3. Interaction scan computation (additive vs actual comparison)
  4. PromotionValidator (QuerySpec verification + evidence promotion)
  5. PlanExecutor integration (residual-triggered sandbox invocation)

Then resume PTV loop from STEP 1 with sandbox enabled.

CRITICAL: sandbox output must NEVER appear in final report without
promotion through PromotionValidator + SQLGuard + Repository.
```

## Session 5 Prompt (Iteration 6 — Final Export)

```text
Read docs/iteration-prompts/12-phase-c-complex-causal-coverage.md
in full. Execute Iteration 6: PTV data export, CI gate, three-layer
readiness verification.

Read docs/ptv/05-grpo-bridge.md for the three-layer consumption model.

You are on branch codex/c-complex-causal with all prior iterations
complete and eval passing.

  1. Run make eval-regression, verify GRPO trajectories
  2. Layer 1: verify optimization_summary.json in every PTV round,
     check fix traceability (each fix links to a summary pattern)
  3. Layer 2: verify sub-agent GRPO trajectory quality (Signal A + B)
  4. Layer 3: verify coding model trajectory quality (Signal C —
     diagnosis entries have fix metadata)
  5. Add CI targets (make test-e2e, make eval-ci)
  6. Write final eval report with per-layer breakdown
  7. Update docs (COMPLIANCE_MATRIX, MetricRCA.md)
  8. Create final commit

After completion, run the full validation:
  make test && make test-e2e && make eval-regression
All must pass. Create PR against main.
```

## Post-Session Adversarial Review Checklist

After EACH Codex session, Claude reviews:

```
CODE INTEGRITY:
□ git diff main...codex/c-complex-causal — full diff read
□ scorer.py changes are additive scoring metrics only (no gate weakening)
□ grpo_dataset.py changes are additive only (no reward logic weakening)
□ regression_public_cases.jsonl has NO answer-bearing fields
□ anomaly_injection.py: existing functions unchanged, new functions pure
□ No keyword/regex intent parsers added
□ No raw SQL outside QuerySpec path
□ No silent fallbacks (grep for 'except.*continue')

ENUM/POLICY COMPLETENESS:
□ Every root_cause_type in ground truth exists in RootCauseType enum
□ Every root_cause_type has matching policy in DEFAULT_POLICY_REGISTRY
□ Every signal_type referenced has matching MetricSignalPolicy
□ Every new action kind is registered in plan_executor + sdk_tools

EVAL INTEGRITY:
□ make test passes independently
□ Original 28 cases still 28/28 in eval output
□ New cases: per-family breakdown meets gates
□ GRPO trajectories have valid schema
□ ADLs recorded for non-trivial decisions

PTV INTEGRITY (Session 3 — run docs/ptv/06-enforcement.md checklist):
□ ARTIFACT ISOLATION:
  □ All artifacts under eval_out/ptv/cycle-{id}/round-{N}/
  □ meta.json exists with cycle metadata
  □ Each round directory has: predictions.jsonl, eval-result.json,
    gap_report.json, diagnosis.jsonl, ptv_trajectory.jsonl
  □ round > 1 directories have prediction_diff.json
  □ summary.json or escalation.json exists at cycle level
□ ANTI-CHEAT (from docs/ptv/06-enforcement.md):
  □ DETECT-1: predictions do NOT match ground truth
  □ DETECT-2: predictions differ between consecutive rounds
  □ DETECT-3: fix commit exists between every pair of rounds
  □ DETECT-4: diagnosis.jsonl present for every round with failures
  □ DETECT-5: unique reasoning count >= 50% of case count
  □ DETECT-6: optimization_summary has controller_rules_applied (round > 1)
  □ DETECT-7: no fix_category appears ≥3 consecutive rounds
□ SUBAGENT DISPATCH:
  □ Prediction Agent wrote predictions (not Controller copy-paste)
  □ PTV Analyst wrote diagnosis AND stall_analysis (not Controller skip)
  □ Eval Agent ran independently (not mixed with diagnosis)
  □ Analyst stall_analysis has recommended_next_category (round > 1)
□ CONTROLLER DECISION RULES (round > 1):
  □ RULE-C1: regressed categories listed in rule_c1_blocked_categories
  □ RULE-C2: categories deferred ≥2 rounds were promoted or escalated
  □ RULE-C3: FIX-D selected before FIX-A when both present
  □ RULE-C4: revert_assessment written when ≥2 metrics regressed
  □ RULE-C5: no category applied >2 consecutive rounds
  □ No "stabilize X first" justification used after 2 rounds of X with regression
  □ Controller's selected_fix_category matches Analyst recommendation
    OR justification explains why it diverged (referencing RULE-C)
□ LAYER 1 (AGENT OPTIMIZATION CONTEXT):
  □ optimization_summary.json exists for every PTV round
  □ failure_patterns derived from cross-case gap clustering
  □ fix decisions traceable to summary patterns (not ad-hoc)
  □ next_optimization_target specifies layer + files + rationale
  □ controller_rules_applied section is complete and accurate
□ LAYER 2/3 (GRPO READINESS):
  □ ptv_trajectory.jsonl records prediction accuracy per round
  □ ptv_trajectory.jsonl includes stall_analysis (round > 1)
  □ Signal A: task trajectories have valid reward and gate fields
  □ Signal B: prediction trajectories extractable from predictions + actuals
  □ Signal C: diagnosis entries have fix_category + fix_commit + root_cause_analysis
```
