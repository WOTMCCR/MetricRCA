# Phase C Iterations 2-3 Plan

## Scope

Execute Session 2 only from `docs/iteration-prompts/12-phase-c-complex-causal-coverage.md`.

## Iteration 2

- Add at least 16 complex causal eval cases across multi-cause, interaction, lagged, and weak-signal families.
- Keep public JSONL free of answer-bearing fields.
- Add private/eval DB ground truth using `root_causes` JSON weights.
- Preserve legacy single-cause ground truth behavior.
- Verify with focused tests, `make test`, and `make eval-regression`.
- Commit as `feat(phase-c/iter-2): ...`.

## Iteration 3

- Extend `PlanCompiler` to produce parallel drilldown/contribution chains for broad multi-dimensional analysis.
- Add a `ContributionSetBuilder` that merges cross-chain contribution sets into canonical `E4`.
- Add a Reflection cross-chain consistency check.
- Preserve explicit-slice behavior and the original 28 regression cases.
- Verify with focused tests, `make test`, and `make eval-regression`.
- Commit as `feat(phase-c/iter-3): ...`.

## Validation Notes

- Use `PATH=.venv/bin:$PATH` for Python/Make commands.
- Seed with `make seed SEED_PROFILE=regression` before eval gates.
- Do not modify `metric_rca/evals/scorer.py` or `metric_rca/evals/grpo_dataset.py`.
- Do not call Claude unless required by the latest checklist and local CLI availability allows it.
