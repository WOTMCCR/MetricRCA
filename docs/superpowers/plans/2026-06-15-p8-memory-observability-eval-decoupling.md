# P8 Memory Observability Eval Decoupling Implementation Plan

**Goal:** Complete Prompt 9 P8 across memory v2, observability/UI, and eval-backend decoupling without weakening P6/P7 gates.

**Acceptance criteria:**
- `memory_record` supports semantic, episodic, reflection, and legacy case layers, with seed-derived semantic records and non-evidentiary retrieval.
- Run/API/eval summaries expose real token and latency data from `trace_step.token_usage` and persisted trace latency.
- React/Vite renders read-only Adtributor candidates, memory layers, and token/latency panels from API data.
- HTTP eval client uses only API endpoints, per-request LLM overrides, embedded `cases.jsonl` expected fields, and local persisted-artifact scoring.
- Direct `make eval` remains functional; `make eval-http PROVIDER=openai MODEL=gpt-5-nano` is additive.

**Primary files/systems:** `docs/MetricRCA.md`, `docs/COMPLIANCE_MATRIX.md`, `metric_rca/data/seed_data.py`, `metric_rca/memory/memory_repo.py`, `metric_rca/agent/runner.py`, `metric_rca/api/*`, `metric_rca/evals/*`, `frontend/src/*`, `Makefile`, tests.

**Validation:** targeted pytest for memory/API/eval/trace/UI first, then `PATH=.venv/bin:$PATH python -m pytest -q`, frontend vitest, review scans A/E, subagent review, Claude CLI review if available.

## Task 1: Docs And Matrix Gate

**Addresses:** P8 prompt docs-first, rows 20-23/26/33 plus new P8 rows.

**Files:** `docs/MetricRCA.md`, `docs/COMPLIANCE_MATRIX.md`, this plan, `docs/env-setup.md`, `AGENTS.md`.

**Work:** Document memory four-layer behavior, token/latency aggregation, eval-http client, per-request LLM override, and GPT-5 Nano eval policy. Add matrix rows for proof tests and shortcuts.

**Validation:** `git diff -- docs/MetricRCA.md docs/COMPLIANCE_MATRIX.md`.

## Task 2: Memory V2

**Addresses:** semantic seed, episodic write, reflection write, case compatibility, retrieval eval, pollution control.

**Files:** `metric_rca/data/seed_data.py`, `metric_rca/memory/memory_repo.py`, `metric_rca/agent/runner.py`, `metric_rca/evals/scorer.py`, `metric_rca/evals/runner.py`, memory tests.

**Work:** Seed semantic records from persisted metric/schema metadata, freeze invalid/null legacy layers to `case`, read layered records, write episodic on succeeded/no_anomaly finalization, write reflection on failed or repaired runs, and score memory paired comparisons without allowing memory ids as evidence.

**Validation:** tests for semantic records changing with metadata, episodic/reflection write gates, layer reads, pollution checks, and paired retrieval summary.

## Task 3: Observability And UI

**Addresses:** token/latency aggregation, eval summary enrichment, three P8 panels.

**Files:** `metric_rca/repositories/metric_repository.py`, `metric_rca/api/routes.py`, `metric_rca/api/schemas.py`, `metric_rca/evals/scorer.py`, `frontend/src/App.tsx`, `frontend/src/apiClient.ts`.

**Work:** Aggregate real `trace_step.token_usage` and `latency_ms` into run responses and eval case details. Render Adtributor EP/surprise candidates, memory layer viewer, and token/latency dashboard using injected API data only.

**Validation:** API tests for token_summary and memory endpoint, scorer tests for per-case token/latency, vitest panel tests.

## Task 4: Eval HTTP Client And Per-Request LLM

**Addresses:** ADL-0012 per-request LLM and eval-backend decoupling.

**Files:** `metric_rca/api/schemas.py`, `metric_rca/api/dependencies.py`, `metric_rca/api/routes.py`, `metric_rca/evals/client.py`, `metric_rca/evals/cases.jsonl`, `Makefile`, eval tests.

**Work:** Add optional request-scoped LLM fields without logging/persisting/returning API keys. Implement pure `httpx.Client(trust_env=False)` eval client that posts runs, fetches artifacts, scores locally, and accepts CLI `--provider openai --model gpt-5-nano`.

**Validation:** API override tests, source scan that eval client does not import `run_rca` or repositories, mocked HTTP client tests, `make eval-http` command parse.

## Task 5: Verification And Review

**Addresses:** evidence-before-done and required review gates.

**Files:** changed files and review artifacts.

**Work:** Run targeted tests, full pytest, frontend tests, review checklist A/E scans, subagent review, and Claude CLI review with `--model opus --effort high` when available.

**Stop/ask if:** live LLM/API credentials or a running backend are unavailable for real `make eval` / `make eval-http`; report exact command failures rather than substituting fake provider results.
