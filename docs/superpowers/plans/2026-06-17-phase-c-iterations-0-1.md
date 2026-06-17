# Phase C Iterations 0-1 Implementation Plan

**Goal:** Complete Phase C session 1 cleanup/E2E harness and additive complex anomaly injections without changing scorer/GRPO logic or existing anomaly dates.

**Acceptance criteria:**
- Baseline `make test` passes and regression eval remains 28/28 before iteration work.
- Iteration 0 removes the legacy eval cases path, isolates legacy E4 compatibility under tests, proves `root_causes` migration, adds E2E smoke test and `make test-e2e`.
- Iteration 1 adds pure additive injection functions for multi-cause, interaction, lagged, and weak-signal dates, with tests proving existing date behavior is unchanged.
- Each iteration gate passes and is committed with `feat(phase-c/iter-N):`.

**Primary files/systems:**
- `metric_rca/evals/runner.py`
- `metric_rca/data/schema.sql`
- `metric_rca/data/seed_data.py`
- `metric_rca/data/anomaly_injection.py`
- `metric_rca/runtime/ranking.py`
- `metric_rca/reporting/projector.py`
- `tests/test_eval.py`, `tests/test_schema.py`, `tests/test_seed.py`, `tests/test_e2e_smoke.py`, `tests/legacy_migration/`
- `Makefile`

**Validation:**
- `PATH=.venv/bin:$PATH make up`
- `PATH=.venv/bin:$PATH make seed SEED_PROFILE=regression`
- `PATH=.venv/bin:$PATH make test`
- `PATH=.venv/bin:$PATH make test-e2e`
- `PATH=.venv/bin:$PATH make eval-regression`

## Task 1: Baseline Gate

**Addresses:** Phase C prelude and regression safety.
**Files:** `eval_out/phase-c-baseline.txt`.
**Work:** Start MySQL, seed regression data, run full test suite, run regression eval, and record current commit.
**Validation:** Baseline commands above.
**Stop/ask if:** Regression eval cannot run because LLM/provider credentials are unavailable.

## Task 2: Iteration 0 Cleanup And Harness

**Addresses:** Phase prompt Iteration 0 and compliance rows 6, 9, 10.
**Files:** eval runner, schema/seed migration, runtime/report legacy projection tests, E2E smoke test, Makefile.
**Work:** Remove `LEGACY_CASES_PATH`, add negative import test, isolate legacy E4 compatibility into `tests/legacy_migration/`, enforce production v3 contribution-set path, prove root-causes column migration, and add a MySQL-backed smoke test target.
**Validation:** `make test`, `make test-e2e`, import-negative test, legacy migration test existence.
**Stop/ask if:** Existing dirty worktree contains conflicting changes in the same functions that cannot be reconciled without reverting user work.

## Task 3: Iteration 1 Additive Injection

**Addresses:** Phase prompt Iteration 1 and data generation purity.
**Files:** `metric_rca/data/anomaly_injection.py`, `metric_rca/data/seed_data.py`, injection/unit tests.
**Work:** Add new date constants and pure functions, hook them only for new dates during fact generation, and test deterministic multipliers plus unchanged existing target/borderline/spike behavior.
**Validation:** `make test`, regression seed, regression eval 28/28.
**Stop/ask if:** New injection hooks alter existing target date generated row values.
