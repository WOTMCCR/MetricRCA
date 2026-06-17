# Phase B Eval Optimization Implementation Plan

**Goal:** reach 28/28 eval green on two consecutive runs through PTV rounds without modifying eval-standard files.

**Acceptance criteria:**
- 28-case eval passes twice consecutively.
- Original C01-C20 stay green every round.
- `make test` passes after fixes.
- No Python keyword/regex intent mapper, raw SQL bypass, metadata constants, or silent fallback is introduced.
- Review checklist sections A and E are scanned with actual output.

**Primary files/systems:**
- `metric_rca/evals/prediction.py`, `metric_rca/evals/gap_analyzer.py`
- `metric_rca/services/metric_service.py`
- `metric_rca/services/anomaly_service.py`
- `metric_rca/agent/prompts.py`
- `metric_rca/agent/middleware.py`
- `metric_rca/agent/tools/`
- `tests/`

**Validation:**
- `PATH=.venv/bin:$PATH python -m metric_rca.evals.prediction eval_out/eval-bN/predictions.jsonl`
- `PATH=.venv/bin:$PATH make eval-stream EVAL_ID=eval-bN`
- `PATH=.venv/bin:$PATH make eval-gaps EVAL_ID=eval-bN`
- `PATH=.venv/bin:$PATH make test`
- Final: review checklist A/E scans and two consecutive `PATH=.venv/bin:$PATH make eval` runs.

## Task 1: Inspect Current State

**Addresses:** compliance rows 11-14, 28-33, 38, 39.
**Files:** eval cases, scorer, runner, prompts, services, agent tools, tests.
**Work:** verify 28-case harness is present and locate intent/expert prompt and anomaly detection boundaries.
**Validation:** source inspection only; no eval-standard files are edited.
**Stop/ask if:** the branch does not contain the 28-case harness.

## Task 2: Run PTV Rounds

**Addresses:** Phase B PTV contract and ADL-0035.
**Files:** `eval_out/eval-bN/predictions.jsonl`, eval output artifacts.
**Work:** write 5-aspect predictions for all 28 cases, validate schema, run `eval-stream`, run `eval-gaps`, then diagnose failures from per-case artifacts.
**Validation:** prediction validator exits 0; gap report exists for each round.
**Stop/ask if:** eval cannot run due non-repairable environment or credentials failure after allowed repair attempts.

## Task 3: Implement Gap-Driven Fixes

**Addresses:** FIX-I/FIX-T/FIX-P/FIX-G taxonomy.
**Files:** targeted prompt, service, guard, or tool modules plus tests.
**Work:** add or update tests that fail against the observed shortcut, implement minimal fix, record ADLs for non-trivial behavior changes.
**Validation:** targeted tests and `make test` pass before the next eval round.
**Stop/ask if:** a proposed fix requires modifying eval-standard files or adding natural-language semantic parsing outside the LLM intent prompt.

## Task 4: Finalize

**Addresses:** acceptance gates and review requirements.
**Files:** `docs/reference/decisions.md`, final eval artifacts, review outputs.
**Work:** confirm two consecutive 28/28 eval passes, run checklist A/E scans, run local verification and requested reviews.
**Validation:** `make test`, two consecutive `make eval`, review checklist scans clean.
**Stop/ask if:** six rounds still fail; run Claude Code `--model opus --effort high` review before continuing.
