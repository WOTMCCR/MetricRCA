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
  3. docs/ptv/02-workflow.md               — loop structure, artifact isolation, subagent dispatch
  4. docs/ptv/03-prediction-protocol.md    — prediction rules R1-R5
  5. docs/ptv/04-diagnosis-protocol.md     — diagnosis schema, fix taxonomy
  6. docs/ptv/06-enforcement.md            — anti-cheat detection (you will be audited)
  7. docs/ptv/bindings/metricrca.md        — MetricRCA-specific aspects, commands, exit conditions
  8. docs/iteration-prompts/12-phase-c-complex-causal-coverage.md (ITERATION 4 section only)

Execute Iteration 4: PTV optimization loop.

You are on branch codex/c-complex-causal with Iterations 0-3 and
review fixes complete. Run make seed && make test to verify baseline.

EXECUTION INSTRUCTIONS:

1. Create a PTV cycle directory:
     CYCLE_ID=$(date +%Y%m%d-%H%M)
     mkdir -p eval_out/ptv/cycle-${CYCLE_ID}
   Write meta.json with branch, base_commit, total_cases.

2. Use the 3-AGENT SUBAGENT DISPATCH pattern from docs/ptv/02-workflow.md:
   For each PTV round:
     a. DISPATCH Prediction Agent → reads code, writes predictions.jsonl
        to eval_out/ptv/cycle-{id}/round-{N}/
     b. DISPATCH Eval Agent → runs make eval-stream, writes eval-result.json
        and per-case artifacts to same round directory
     c. DISPATCH PTV Analyst Agent → runs gap analyzer, reads predictions +
        eval results, writes gap_report.json + diagnosis.jsonl +
        ptv_trajectory.jsonl to same round directory
     d. Controller reads diagnosis, decides fix or escalate
     e. If fix: implement, test, commit, write fix_commit.txt
     f. Increment round, go to step (a)

   If your runtime does not support subagents, run steps (a)-(c)
   sequentially but NEVER skip diagnosis.

3. All PTV artifacts MUST go into eval_out/ptv/cycle-{id}/round-{N}/.
   Do NOT scatter artifacts across eval_out/{eval_id}/ directories.

4. After the session, your artifacts will be reviewed against the
   anti-cheat checklist in docs/ptv/06-enforcement.md. Specifically:
     - DETECT-1: predictions must NOT match ground truth
     - DETECT-2: predictions must differ between rounds
     - DETECT-3: every round must have a fix commit
     - DETECT-4: diagnosis.jsonl must exist for every round with failures
     - DETECT-5: reasoning must be case-specific, not templated

5. EXIT when MetricRCA gates pass (see bindings/metricrca.md).
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
in full. Execute Iteration 6: final GRPO export and CI gate.

You are on branch codex/c-complex-causal with all prior iterations
complete and eval passing.

  1. Run make eval-regression, verify GRPO trajectories
  2. Verify multi-cause trajectory quality
  3. Add CI targets (make test-e2e, make eval-ci)
  4. Write final eval report
  5. Update docs (COMPLIANCE_MATRIX, MetricRCA.md)
  6. Create final commit

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
□ SUBAGENT DISPATCH:
  □ Prediction Agent wrote predictions (not Controller copy-paste)
  □ PTV Analyst wrote diagnosis (not Controller skip)
  □ Eval Agent ran independently (not mixed with diagnosis)
□ GRPO READINESS:
  □ ptv_trajectory.jsonl records prediction accuracy per round
  □ Signal B trajectories extractable from predictions + actuals
  □ Signal C trajectories extractable from diagnosis entries
```
