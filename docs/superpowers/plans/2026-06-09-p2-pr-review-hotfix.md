# P2 PR Review Hotfix Implementation Plan

**Goal:** Address GPT Pro blocking review findings on PR #1 without expanding into P3 graph, API, UI, or eval runner scope.

**Acceptance criteria:**
- Tool SQL execution failures return typed `Observation(ok=False, error_code=...)` and do not fabricate evidence.
- Metadata-only service methods work without an OpenAI API key; only `parse_question` requires the live LLM planner.
- `calculate_contribution` does not run GMV-only decomposition for non-GMV metrics.
- Runtime metadata proof tests use the real `MetadataRepository` and seeded DB, not only unit fakes.
- Review gates cover runtime hardcode risks, renderer/domain/metadata consistency, configured rule overrides, A/E scans, local tests, and subagent review.

**Primary files/systems:** `metric_rca/agent/tools/*`, `metric_rca/services/metric_service.py`, `metric_rca/services/intent_planner.py`, `metric_rca/guardrails/*`, `metric_rca/repositories/metadata_repository.py`, `metric_rca/config/settings.py`, `tests/*`, `docs/COMPLIANCE_MATRIX.md`, `docs/reference/decisions.md`.

**Validation:** targeted tests first, then `PATH=.venv/bin:$PATH make seed`, `PATH=.venv/bin:$PATH make test`, mandatory checklist A/E grep scans, subagent code review, commit and push to the existing PR branch.

## Task 1: Tool Runtime Typed Errors

**Addresses:** Blocking 1; compliance rows 12, 16, 27; checklist C2-C4, E4.

**Files:** `metric_rca/agent/tools/runtime.py`, `metric_rca/agent/tools/{detect_anomaly,drilldown_dimension,fetch_related_signal,calculate_contribution}.py`, `tests/test_tools.py`.

**Work:** Add a shared runtime helper for run validation, current-run evidence validation, guarded plan execution, evidence row construction, and query source summaries. Convert `SQL_GUARD_REJECTED`, `SQL_PLAN_INVALID`, and `SQL_EXECUTION_FAILED` from repository boundaries into typed tool observations. Do not retry, continue, or create evidence on failure.

**Validation:** Add tests that monkeypatch `execute_plan` to raise each repository error and assert the tool returns the corresponding typed error with no evidence rows.

## Task 2: Metadata Service Does Not Require LLM Construction

**Addresses:** Blocking 2; compliance rows 12, 27; §13 metadata contracts.

**Files:** `metric_rca/services/metric_service.py`, `tests/test_metadata_service.py`, `tests/test_zero_fallback.py`.

**Work:** Lazy-create the `LLMIntentPlanner` inside `parse_question()` only. `get_metric_definition()`, `get_schema_context()`, and supported metadata access remain available without an API key. `parse_question()` still fails fast with `LLM_REQUIRED_UNAVAILABLE` when the LLM is required but unavailable.

**Validation:** Add a no-API-key metadata test and keep negative parse tests asserting typed LLM failure.

## Task 3: Metric-Specific Contribution Behavior

**Addresses:** Blocking 3; compliance row 18.

**Files:** `metric_rca/agent/tools/calculate_contribution.py`, `tests/test_tools.py`, possibly `metric_rca/services/attribution_service.py`.

**Work:** Restrict UV/PAY_CVR/AOV factor decomposition to `gmv`. For non-GMV metrics, return a metric-specific contribution summary based on the current metric drilldown evidence without GMV factor fields. Avoid unsupported fabricated E4 factor explanations.

**Validation:** Add tests for `pay_cvr`, `refund_rate`, and `net_gmv` contribution results that assert no GMV decomposition or fact_traffic GMV factor queries are present.

## Task 4: Runtime Metadata and Consistency Gates

**Addresses:** Blocking 4; hardcode/fallback risk table.

**Files:** `tests/test_metadata_service.py`, `tests/test_seed.py`, `tests/test_project_contract.py`, `docs/COMPLIANCE_MATRIX.md`.

**Work:** Add real-repo runtime mutation proof through `MetricService`, renderer/domain/seeded metadata consistency tests, and explicit config override tests for signal/root-cause mappings. Keep test fakes quarantined as unit fixtures and do not present them as metadata-hardcode proof.

**Validation:** Targeted tests plus checklist A/E scans.

## Task 5: Review, Commit, Push

**Addresses:** User request to rerun gates, subagent review, and refresh PR.

**Files:** git branch `codex/p2-metadata-llm-planner`.

**Work:** Run local verification, run subagent review with GPT Pro findings and diff context, fix any blocking findings, commit, push. Update the existing draft PR branch.

**Stop/ask if:** Required live OpenAI calls fail due to missing/invalid API credentials or provider outage; report the exact typed failure and do not substitute mocks.
