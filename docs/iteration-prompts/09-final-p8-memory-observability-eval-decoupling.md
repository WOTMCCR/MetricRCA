# Prompt 9 — Final P8: Memory v2 + Observability + Eval-Backend Decoupling

```text
You are implementing Final P8 in the MetricRCA repo. Branch:
codex/p8-memory-observability from the merged P7 head on main.

MANDATORY PRELUDE — read and obey before touching anything:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md
3. docs/final-design/ (ALL five files). P8-critical:
   - 02-interfaces §5 (memory v2 four-layer), §7 (Settings + per-request LLM)
   - 01-architecture §8 (observability)
   - 04-phase-plan "P8"
4. docs/reference/decisions.md ADL-0012 (eval decoupling, per-request LLM,
   model policy), ADL-0011 (multi-provider), ADL-0009 (eval integrity)

ENVIRONMENT — uv; network good; LLM creds configured.
  uv venv .venv && uv pip install -e .
  PATH=.venv/bin:$PATH throughout. deepagents is a hard dep.

SOURCE OF TRUTH PRIORITY
1. Current user instruction
2. docs/final-design/ (v2 design)
3. docs/reference/decisions.md (ADL-0012 is binding for this phase)
4. docs/MetricRCA.md, docs/COMPLIANCE_MATRIX.md

═══════════════════════════════════════════════════════════════════
TRACK A: MEMORY V2 (four-layer + retrieval eval + pollution control)
═══════════════════════════════════════════════════════════════════

A1. SEMANTIC LAYER — seed-time generation from metadata.
    - `make seed` must populate memory_record rows with layer="semantic" for
      metric aliases, business rules, dimension meanings.
    - Source: MetadataRepository (metric_definition, schema_context).
    - No hardcoded semantic records in code; derive from persisted metadata.

A2. EPISODIC LAYER — write on run finalization.
    - RunOrchestrator._finalize writes a memory_record(layer="episodic") with
      case summary: metric_id, dimension, root_cause_type, verdict, run_id.
    - Only for succeeded/no_anomaly runs; failed runs do NOT write episodic.

A3. REFLECTION LAYER — write on run failure or repair.
    - RunOrchestrator writes memory_record(layer="reflection") with error_code,
      reflection issues, gap description.
    - Only for failed runs or runs that required repair.

A4. CASE LAYER — freeze existing v1 memory_records as layer="case" (read-only).
    - Migration: UPDATE existing records SET layer="case" if layer is NULL or
      not one of semantic/episodic/reflection.
    - MemoryRepository.read must still return case-layer records.

A5. RETRIEVAL INFLUENCE — memory hits adjust drilldown priority, NOT evidence.
    - Expert prompt receives memory context with confidence scores.
    - reflection_factor ≤ 1.2; memory hit is never an evidence_id.
    - memory_pollution_ok eval check: no memory_id appears in any
      candidate.evidence_ids or report numeric_claims.

A6. MEMORY RETRIEVAL EVAL — paired comparison.
    - For each case: run ONCE with memory enabled (episodic/reflection from prior
      runs), ONCE with memory disabled (memory_enabled=False).
    - memory-enabled correct rate must be ≥ memory-disabled correct rate.
    - Zero pollution assertion: no candidate.evidence_ids contains memory_id.
    - Add to eval summary: memory_hit_improvement, memory_pollution_ok.

═══════════════════════════════════════════════════════════════════
TRACK B: OBSERVABILITY ENHANCEMENTS
═══════════════════════════════════════════════════════════════════

B1. TOKEN/LATENCY AGGREGATION — per-run summary.
    - agent_run gets optional JSON fields: total_tokens, total_latency_ms,
      token_breakdown (prompt/completion per LLM call from trace_step.token_usage).
    - API: RunResponse gains optional token_summary field.

B2. EVAL SUMMARY ENRICHMENT
    - eval_run.summary gains: avg_tokens_per_case, avg_latency_per_case,
      provider, model (already partially done per ADL-0011).
    - Per-case detail: token_count, latency_ms, adtributor_used, multi_agent_path.

B3. UI PANELS (3 new panels on existing React/Vite frontend).
    - Adtributor candidates panel: EP/surprise sorted candidates from E_rank.
    - Memory layer viewer: semantic/episodic/reflection/case records for a run.
    - Token/latency dashboard: per-run and per-step breakdown.
    - All panels read-only from existing API endpoints; no new data fabrication.
    - Test with injectable fake API client (no real network in tests).

═══════════════════════════════════════════════════════════════════
TRACK C: EVAL-BACKEND DECOUPLING (ADL-0012)
═══════════════════════════════════════════════════════════════════

C1. PER-REQUEST LLM OVERRIDE — API layer.
    - RunCreateRequest adds optional: llm_provider, llm_model, llm_api_key.
    - routes.py: settings_with_overrides passes these to Settings for the run.
    - When provided, these override env-var defaults for that single run only.
    - Security: llm_api_key is not logged, not persisted, not returned in response.

C2. EVAL HTTP CLIENT — new metric_rca/evals/client.py.
    - Pure HTTP client: reads cases.jsonl, sends POST /api/rca/runs per case,
      reads results via GET /api/rca/runs/{id}, GET .../evidence, GET .../trace.
    - Scores locally using existing scorer.py.
    - Ground truth: embedded in cases.jsonl (add expected_metric_id,
      expected_anomaly, expected_root_cause_type, expected_dimension,
      expected_element fields). Client does NOT need DB access.
    - CLI: python -m metric_rca.evals.client --base-url http://localhost:8000 \
           --provider openai --model gpt-5-nano
    - Makefile: `make eval-http BASE_URL=... PROVIDER=... MODEL=...`

C3. DUAL-PROVIDER EVAL PROOF — acceptance requirement.
    - Run eval-http against the same running backend with TWO different
      provider/model combos (e.g. openai/gpt-5-nano + deepseek/deepseek-chat).
    - Both must meet thresholds. Paste both summary JSONs.
    - If one provider fails threshold, diagnose; do NOT weaken thresholds.

C4. BACKWARDS COMPATIBILITY — existing make eval still works.
    - make eval (direct-call mode) remains unchanged and functional.
    - make eval-http is additive.

DO-NOT-REGRESS INVARIANTS (re-verify):
- All P6/P7 tests still green (262+).
- 20-case eval still green in direct mode.
- QuerySpec→SQLRenderer→SQLGuard→Repository sole data path.
- MetadataRepository→MetricService sole metadata path.
- ADL-0006 projection invariant.
- Filesystem tools excluded (ToolNode proof test green).
- METRIC_SCOPE_VIOLATION guard functional.
- Eval questions remain natural-language (§9.1 zero leakage).

DOCS-FIRST (commit BEFORE implementation commits):
- Update docs/MetricRCA.md: memory v2 four-layer, observability enhancements,
  eval-http client, per-request LLM.
- Update docs/COMPLIANCE_MATRIX.md: rows for memory four-layer, episodic write,
  reflection write, semantic seed, memory retrieval eval, pollution control,
  token/latency aggregation, eval-http client, per-request LLM override.

ACCEPTANCE — evidence-before-done; paste ACTUAL output for each:
1. PATH=.venv/bin:$PATH python -m pytest -q → all green, count > P7's total.
   Include: memory four-layer tests, episodic/reflection write tests,
   semantic seed test, memory pollution test, token aggregation test,
   per-request LLM override test, eval-http client test (mocked HTTP).
2. Memory retrieval eval: paired run output showing memory_hit_improvement ≥ 0
   and memory_pollution_ok = true.
3. Direct eval (make eval): 20-case still green, thresholds_met=true.
4. HTTP eval (make eval-http): 20-case green with at least ONE provider.
   If two providers available, paste BOTH summary JSONs.
5. UI panel screenshots or test output proving 3 new panels render.
6. 06-review A+E scans (zero hits).

FORBIDDEN SHORTCUTS (any = reject):
- Memory-derived evidence (evidence_ids containing memory_id).
- Hardcoded semantic memory records in code (must come from metadata seed).
- Fabricated token/latency data (must come from real trace_step.token_usage).
- eval-http client that imports run_rca or MetricRepository directly.
- Logging or persisting llm_api_key from per-request override.
- Weakening P7 eval thresholds or question integrity.
- Faking dual-provider results from same provider.

DELIVERABLE: ordered commit list (docs/matrix first, then code), each with
message + file list, plus pasted acceptance output. End with honest status.
```
