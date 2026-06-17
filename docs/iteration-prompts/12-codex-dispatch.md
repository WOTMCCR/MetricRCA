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
Read docs/iteration-prompts/12-phase-c-complex-causal-coverage.md
in full. Execute Iteration 4: PTV optimization loop.

You are on branch codex/c-complex-causal with Iterations 0-3 complete.
Run make seed && make eval-regression to verify baseline.

Run the PTV loop:
  round=1
  PREDICT: write predictions for ALL cases (original 28 + new 16+)
  EXECUTE: make eval-stream EVAL_ID=eval-c{round}
  VERIFY: make eval-gaps EVAL_ID=eval-c{round}
  CHECK: are exit conditions met?
  DIAGNOSE: for failures, determine fix type (FIX-I/G/T/P/D/M/A/B)
  FIX: implement minimal fix
  make test (must pass)
  round += 1, loop

EXIT when:
  - All families pass per-family gate
  - root_cause_set_recall_avg >= 0.85
  - weighted_explanation_coverage_avg >= 0.85
  - Confirmed by 2 consecutive green runs

ESCALATION: if after 6 rounds, interaction or lagged cases are still
below gate, record escalation ADL and stop. Do not implement Phase D
sandbox in this session.

Commit after each PTV round with "fix(phase-c/ptv-{round}):".
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
□ git diff main...codex/c-complex-causal — full diff read
□ scorer.py UNMODIFIED (sha256 matches main)
□ grpo_dataset.py UNMODIFIED
□ regression_public_cases.jsonl has NO answer-bearing fields
□ anomaly_injection.py: existing functions unchanged, new functions pure
□ No keyword/regex intent parsers added
□ No raw SQL outside QuerySpec path
□ make test passes independently
□ Original 28 cases still 28/28 in eval output
□ New cases: per-family breakdown meets gates
□ GRPO trajectories have valid schema
□ ADLs recorded for non-trivial decisions
□ No silent fallbacks (grep for 'except.*continue')
```
