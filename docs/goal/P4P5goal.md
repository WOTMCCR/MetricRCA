GOAL: Finish MetricRCA Matrix P4 + P5 end-to-end, preserving the completed P1/P2/P3 architecture and zero-fallback guarantees.

Repository: WOTMCCR/MetricRCA
Base branch: main
Create branch: codex/p4-p5-api-ui-eval-final
Do not use Claude/Opus/ review unless explicitly requested by the project owner in a later message. 但是你可以用 subagent review

You are working after P3B has been merged. P3B established:
- real LangGraph StateGraph/ReAct/Trace
- deterministic action/tool/signal registry
- Reflection verifier with repair through legal ReAct/tool/query/evidence path
- real MemoryRepository over memory_record
- verified report projection boundary
- persisted Evidence consistency checks
- no memory-derived conclusion
- no report/task after failed Reflection

This goal must complete Matrix P4 and P5:
- Row 21: FastAPI API
- Row 22: Streamlit debug UI
- Row 23: Eval runner/scorer
- Row 25: README + architecture docs + final compliance
- Row 24 final zero-fallback negatives
- Keep rows 1-20, 26, 27 green

Do not claim completion unless Known shortcuts is exactly [].

────────────────────────────────────────────────────────
MANDATORY READING ORDER
────────────────────────────────────────────────────────

Before writing code, read these in order:

1. AGENTS.md
2. docs/IMPLEMENTATION_CONTRACT.md
3. docs/iteration-prompts/00-global-iteration-rules.md
4. docs/iteration-prompts/06-review-checklist.md
5. docs/COMPLIANCE_MATRIX.md rows 21, 22, 23, 24, 25, plus rows 16, 19, 20, 26, 27 for regression boundaries
6. docs/MetricRCA.md §10, §14, §15, §16, §17, §18, §19, §20
7. docs/reference/decisions.md, especially ADL-0005, ADL-0006, ADL-0007 if present
8. docs/iteration-prompts/04-matrix-p4-api-ui.md
9. docs/iteration-prompts/05-matrix-p5-eval-docs.md
10. Current implementation:
    - metric_rca/agent/graph.py
    - metric_rca/agent/react.py
    - metric_rca/agent/reflection.py
    - metric_rca/agent/nodes/generate_report.py
    - metric_rca/agent/tools/registry.py
    - metric_rca/observability/trace.py
    - metric_rca/memory/memory_repo.py
    - metric_rca/repositories/metric_repository.py
    - metric_rca/guardrails/query_spec.py
    - metric_rca/guardrails/renderer.py
    - metric_rca/guardrails/sql_guard.py
    - metric_rca/evals/runner.py
11. Existing tests:
    - tests/test_graph.py
    - tests/test_reflection.py
    - tests/test_memory.py
    - tests/test_react.py
    - tests/test_trace.py
    - tests/test_tools.py
    - tests/test_zero_fallback.py
    - tests/test_project_contract.py

If any instruction conflicts, follow this priority:
current user goal > AGENTS.md / IMPLEMENTATION_CONTRACT > MetricRCA.md > iteration prompts > old comments.

────────────────────────────────────────────────────────
NON-NEGOTIABLE ARCHITECTURE BOUNDARIES
────────────────────────────────────────────────────────

1. Preserve the sole metric-fact data path:
   QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan

2. API, UI, eval, docs must not weaken P3:
   - no route-level hardcoded RCA output
   - no eval hardcoded success
   - no fake Streamlit panels
   - no report from non-persisted graph return state
   - no memory-derived conclusion
   - no runtime anomaly_ground_truth leakage outside seed/eval/tests
   - no broad except Exception returning success
   - no final report numeric claim without persisted Evidence

3. P4 API GET routes must read persisted artifacts:
   - agent_run
   - evidence
   - trace_step
   - sql_audit
   - operation_task
   - eval_run / eval_case_result
   They must not return hardcoded data and must not depend on in-memory state returned by POST.

4. P4 report strategy:
   Implement deterministic reconstruction from persisted artifacts.
   Recommended file:
   - metric_rca/reporting/projector.py

   The projector:
   - must not query fact tables
   - must not call run_rca
   - must not read anomaly_ground_truth
   - must not call LLM
   - must not fabricate reports
   - must reconstruct succeeded reports from persisted E4.result_summary.selected_candidate
   - must reconstruct no_anomaly reports only from agent_run + E1
   - must return no report for failed runs
   - must expose top_candidate identity fields only:
     root_cause_type, dimension, element, verdict
   - must expose all numeric values only under numeric_claims
   - every numeric_claim must bind a persisted evidence_id

5. P5 scorer must score persisted artifacts, not graph in-memory state.
   Eval runner may call run_rca to create a run, but scorer must read:
   - agent_run
   - evidence
   - trace_step
   - sql_audit
   - operation_task
   - reconstructed report from projector

6. P5 dangerous_sql_blocked must be real:
   - call real SQLGuard on dangerous SQL
   - bool only, never null
   - test must fail if guard is monkeypatched/broken
   - no constant True

7. P5 ground truth usage:
   anomaly_ground_truth is allowed only in seed/eval/tests.
   Runtime agent/services/tools/api/reporting/ui must not read it.

8. Bounded SQL retry remains an accepted deviation unless you implement it narrowly:
   - only retry SQL_EXECUTION_FAILED
   - max 2 attempts total
   - do not retry SQL_GUARD_REJECTED, SQL_PLAN_INVALID, QUERY_BUDGET_EXCEEDED, SYSTEM_TABLE_*, MEMORY_*
   If not implemented, list it as a remaining non-P0 deviation.

────────────────────────────────────────────────────────
IMPLEMENTATION ORDER
────────────────────────────────────────────────────────

Do this in sequence. Do not start later steps until earlier step tests exist.

PHASE A — Reporting projector foundation

Create:
- metric_rca/reporting/__init__.py
- metric_rca/reporting/projector.py
- tests/test_reporting.py

Projector required functions:
- build_report_from_persisted_artifacts(agent_run: dict, evidences: list[dict], tasks: list[dict] | None = None) -> dict | None
- project_candidate_from_e4(e4_result_summary: dict) -> dict | None
- numeric_claims_from_e4(e4_result_summary: dict, e4_id: str) -> list[dict]
- evidence_by_alias(evidences: list[dict], run_id: str) -> dict[str, dict]

Behavior:
- succeeded run:
  - requires persisted E4
  - requires E4.result_summary.selected_candidate
  - builds safe report projection
  - top_candidate must not expose numeric fields except via numeric_claims
- no_anomaly run:
  - requires exactly E1
  - no candidate
  - no task
  - returns no_anomaly report
- failed run:
  - returns None
- malformed artifact:
  - fail closed, return None or typed projection error object
  - do not fabricate report

Tests first:
- test_projector_builds_report_from_persisted_e4_selected_candidate
- test_projector_rejects_missing_e4
- test_projector_rejects_malformed_e4
- test_projector_report_has_no_unverified_numeric_fields
- test_projector_no_anomaly_e1_only
- test_projector_failed_run_no_report
- test_projector_no_anomaly_rejects_task_or_candidate

Optional refactor:
- Refactor metric_rca/agent/nodes/generate_report.py to reuse projector.
- If not refactoring, prove projector output and generate_report output have the same safe shape.

PHASE B — Repository read helpers

Update:
- metric_rca/repositories/metric_repository.py

Add parameterized read helpers:
- get_trace_steps(run_id: str) -> list[dict]
- get_evidences(run_id: str) -> list[dict]
- get_sql_audit_rows(run_id: str) -> list[dict]
- get_operation_tasks(run_id: str) -> list[dict]
- get_eval_run(eval_id: str) -> dict | None
- get_eval_case_results(eval_id: str) -> list[dict]

Requirements:
- JSON columns decoded into dict/list.
- DB read failures raise typed RuntimeError("SYSTEM_TABLE_READ_FAILED") or documented eval-specific typed error.
- No string interpolation for values.
- Only whitelist table/column identifiers if any dynamic identifier is needed.
- Do not read fact tables here.

Tests:
- can be in tests/test_api.py or tests/test_reporting.py with lightweight repository fakes/spies.
- API GET tests must fail if helpers are not used.

PHASE C — FastAPI

Create:
- metric_rca/api/__init__.py
- metric_rca/api/main.py
- metric_rca/api/routes.py
- metric_rca/api/schemas.py
- metric_rca/api/dependencies.py if useful
- tests/test_api.py

Update:
- pyproject.toml
- Makefile
- tests/test_project_contract.py

Dependencies:
- fastapi with bounded constraint
- uvicorn with bounded constraint
- httpx with bounded constraint if not already sufficient

Endpoints:

GET /health
- returns {"status": "ok"}

POST /api/rca/runs
Request:
- question: str
- target_date: date | None = None
- business_today: date | None = None
- memory_enabled: bool | None = None
- memory_required: bool | None = None

Behavior:
- invoke run_rca
- must call compiled graph through run_rca, not a local stub
- return run_id, status, error_code, report if available
- POST may use graph return state because it just ran the graph, but all persisted side effects must exist after call

GET /api/rca/runs/{run_id}
- read persisted agent_run
- read persisted evidence
- read persisted operation_task
- reconstruct report with reporting projector
- return run metadata + report + candidate projection + tasks + error_code
- do not use in-memory graph state
- do not hardcode output

GET /api/rca/runs/{run_id}/trace
- read trace_step ordered by seq

GET /api/rca/runs/{run_id}/evidence
- read evidence rows, decoded query_spec/result_summary

GET /api/rca/runs/{run_id}/sql-audit
- read sql_audit rows ordered by audit_id or created_at

GET /api/rca/runs/{run_id}/tasks
- read operation_task rows

GET /api/rca/runs/{run_id}/memory
- return memory status or memory-related trace summaries if available
- do not infer memory conclusion
- do not fabricate memory hits

POST /api/evals/run
- After P5 implementation in this same goal, must run real eval runner or call real eval service.
- If you implement API before eval, temporary NOT_IMPLEMENTED is allowed only inside the same branch before final completion.
- Final state of this goal must not fake eval success.

GET /api/evals/{eval_id}
- read persisted eval_run + eval_case_result
- 404 if absent

Error response model:
- error_code
- message
- recoverable
- retryable
- trace_step_id
- suggested_next_action

Tests first:
- test_health_ok
- test_post_rca_runs_invokes_run_rca_and_persists_agent_run
- test_get_run_reads_persisted_artifacts_not_graph_return_state
- test_get_run_reconstructs_verified_report_from_persisted_e4
- test_get_run_failed_state_returns_error_and_no_report
- test_get_run_no_anomaly_has_e1_only_no_task_no_candidate
- test_get_trace_reads_persisted_trace_ordered_by_seq
- test_get_evidence_reads_persisted_evidence_and_decodes_json
- test_get_sql_audit_reads_persisted_sql_audit
- test_get_tasks_reads_persisted_operation_task
- test_bad_body_returns_422
- test_business_error_response_shape
- test_api_routes_do_not_read_anomaly_ground_truth
- test_eval_endpoint_runs_real_eval_after_p5_or_not_implemented_before_p5
- test_makefile_api_target

PHASE D — Streamlit UI

Create:
- metric_rca/ui/__init__.py
- metric_rca/ui/app.py
- metric_rca/ui/api_client.py if useful
- tests/test_ui_smoke.py

Update:
- pyproject.toml
- Makefile
- tests/test_project_contract.py

UI requirements:
- real Streamlit app
- module import must not start a run
- module import must not make network calls
- normal operation must call API, not internal graph
- httpx client for localhost must use trust_env=False
- API client must be injectable in tests

Required panels:
1. question input
2. conclusion/report
3. root cause Top-K
4. Evidence table
5. SQL audit table
6. Trace timeline
7. Reflection issues
8. Memory hits / memory status
9. Eval summary / eval status

Tests first:
- test_ui_import_has_no_network_side_effect
- test_ui_uses_injected_fake_api_client
- test_ui_renders_9_required_panels_from_fake_api_data
- test_ui_httpx_client_uses_trust_env_false
- test_ui_eval_panel_displays_real_eval_or_not_implemented_cleanly
- test_makefile_ui_target

PHASE E — Eval runner/scorer

Create/update:
- metric_rca/evals/cases.jsonl
- metric_rca/evals/models.py if useful
- metric_rca/evals/scorer.py
- metric_rca/evals/runner.py
- tests/test_eval.py
- tests/test_zero_fallback.py final additions

Cases:
cases.jsonl must include:
- gmv_paid_ads_drop
- gmv_stockout_electronics
- cvr_mobile_drop
- refund_rate_product_quality
- gmv_no_anomaly

Each case:
- case_id
- question
- optional tags
- no authoritative expected answers unless validated against DB GT

Eval runner:
- load cases
- read anomaly_ground_truth from DB
- if GT row missing: EVAL_GROUND_TRUTH_MISSING
- run run_rca once per case
- score from persisted artifacts, not graph return state
- write eval_run
- write eval_case_result per case
- output JSON and Markdown
- exit 0 only when eval completes and thresholds pass
- exit nonzero for missing GT or critical eval failure

Scorer per-case fields:
- intent_ok
- anomaly_ok
- top1_ok
- top3_ok
- evidence_coverage
- sql_safe
- reflection_repair_ok
- report_traceable_ok
- memory_pollution_ok
- no_anomaly_task_ok

Summary fields:
- case_total
- top1_rate
- top3_rate
- anomaly_accuracy
- evidence_coverage_avg
- sql_safe_rate
- report_traceable_rate
- reflection_repair_ok
- memory_pollution_ok
- dangerous_sql_blocked
- no_anomaly_correct

Scoring rules:
- intent_ok:
  agent_run.metric_id == GT metric_id
- anomaly_ok:
  expected_anomaly=false -> status must be no_anomaly
  expected_anomaly=true -> status should be succeeded, with E1 anomaly evidence
- top1_ok:
  reconstructed report or E4.selected_candidate root_cause_type/dimension/element matches GT
- top3_ok:
  any E4.result_summary.candidates entry matches GT
- evidence_coverage:
  top candidate binds current-run E1-E4, each persisted, guard_status=passed
- sql_safe:
  all executed SQL comes from sql_audit and guard_status safe
- reflection_repair_ok:
  if repair occurred, trace shows reflection_verify -> react_step -> execute_tool -> reflection_verify and new evidence/sql_audit exists
- report_traceable_ok:
  every numeric claim in reconstructed report maps to persisted Evidence.result_summary
- memory_pollution_ok:
  no memory id appears in evidence_ids; memory hit cannot create candidate without E1-E4
- no_anomaly_task_ok:
  status=no_anomaly, exactly E1, no operation_task, no attribute_rank trace, no candidate

dangerous_sql_blocked:
- use real SQLGuard on dangerous SQL
- dangerous examples: DELETE, SELECT *, multi-statement
- bool true only if guard rejects
- test monkeypatches/breaks guard and proves value changes

Tests first:
- test_eval_loads_cases_and_ground_truth
- test_eval_missing_ground_truth_returns_EVAL_GROUND_TRUTH_MISSING
- test_eval_mutating_ground_truth_changes_score
- test_eval_runs_rca_for_each_case
- test_eval_scores_from_persisted_artifacts_not_graph_return_state
- test_eval_writes_eval_run_and_eval_case_result
- test_eval_scores_intent_anomaly_top1_top3_evidence_sql_reflection
- test_eval_report_traceable_ok_requires_persisted_numeric_claims
- test_eval_memory_pollution_ok_rejects_memory_evidence_id
- test_dangerous_sql_blocked_is_real_boolean_from_guard
- test_dangerous_sql_blocked_not_constant_when_guard_monkeypatched
- test_no_anomaly_correct_requires_no_task_no_attribute_rank_no_candidate
- test_eval_json_and_markdown_outputs_exist
- test_runtime_code_outside_seed_eval_tests_does_not_read_anomaly_ground_truth
- test_make_eval_no_longer_not_implemented
- test_api_eval_endpoint_runs_or_reads_real_eval

PHASE F — Docs and final compliance

Create/update:
- README.md
- docs/architecture.md
- docs/final-compliance.md or update docs/COMPLIANCE_MATRIX.md with status/proof
- screenshots/README.md or real screenshots
- tests/test_docs_compliance.py

README must document:
- architecture
- QuerySpec -> SQLRenderer -> SQLGuard -> Repository sole data path
- LangGraph/ReAct
- Reflection verifier + repair path
- Memory boundary
- verified report projection
- API persisted artifact contract
- Streamlit UI
- Eval runner/scorer
- Zero Silent Fallback
- make up / seed / api / ui / eval / test
- API endpoints
- Eval metrics
- Error codes
- Target response example
- Known limitations/deviations

docs/architecture.md must include Mermaid diagrams:
1. logical architecture
2. graph control flow
3. ReAct repair path
4. QuerySpec data path
5. persisted artifact/report reconstruction path
6. memory pollution boundary
7. API/UI flow
8. eval pipeline

Final compliance:
- rows 1-27
- status one of:
  satisfied
  partial
  intentionally deferred
  missing
- each row must list proof test or command
- no P0 missing or partial if claiming complete

Screenshots:
- If real UI screenshot possible, add real screenshots.
- If not possible, create screenshots/README.md with exact reproduction commands.
- Do not add fake placeholder images.

Tests first:
- test_readme_commands_match_makefile
- test_readme_endpoints_match_fastapi_routes
- test_readme_error_codes_match_domain_or_api_error_models
- test_architecture_md_has_mermaid_and_matches_required_nodes
- test_architecture_md_mentions_persisted_report_projection
- test_final_compliance_has_rows_1_to_27_with_status_and_proof
- test_docs_do_not_claim_unimplemented_features

────────────────────────────────────────────────────────
FILES EXPECTED TO CREATE OR UPDATE
────────────────────────────────────────────────────────

CREATE:
- metric_rca/api/__init__.py
- metric_rca/api/main.py
- metric_rca/api/routes.py
- metric_rca/api/schemas.py
- metric_rca/api/dependencies.py if useful
- metric_rca/reporting/__init__.py
- metric_rca/reporting/projector.py
- metric_rca/ui/__init__.py
- metric_rca/ui/app.py
- metric_rca/ui/api_client.py if useful
- metric_rca/evals/cases.jsonl
- metric_rca/evals/models.py if useful
- metric_rca/evals/scorer.py
- README.md
- docs/architecture.md
- docs/final-compliance.md
- screenshots/README.md or real screenshots
- tests/test_api.py
- tests/test_reporting.py
- tests/test_ui_smoke.py
- tests/test_eval.py
- tests/test_docs_compliance.py

UPDATE:
- metric_rca/evals/runner.py
- metric_rca/repositories/metric_repository.py
- pyproject.toml
- Makefile
- tests/test_project_contract.py
- tests/test_zero_fallback.py if needed
- docs/reference/decisions.md if new decisions are made

DO NOT DELETE:
- existing P1/P2/P3 tests
- existing guardrails
- existing agent graph/nodes/tools
- existing data seed/schema unless a documented migration is necessary

DO NOT MERGE:
- Do not collapse graph nodes into graph.py.
- Do not merge API/UI/eval logic into agent graph.
- Do not merge eval scorer into API route.

────────────────────────────────────────────────────────
FINAL COMMANDS TO RUN
────────────────────────────────────────────────────────

Run these before final response:

PATH=.venv/bin:$PATH make up
PATH=.venv/bin:$PATH make seed
PATH=.venv/bin:$PATH pytest -q tests/test_reporting.py tests/test_api.py tests/test_ui_smoke.py tests/test_eval.py tests/test_docs_compliance.py
PATH=.venv/bin:$PATH make eval
PATH=.venv/bin:$PATH make test
PATH=.venv/bin:$PATH python -W error::ResourceWarning -m unittest discover -s tests -v
git diff --check

Run review checklist A/E grep scans exactly:

grep -rn "METRIC_DEFINITIONS\|MetricDefinition(" \
  metric_rca/services/ metric_rca/agent/ \
  --include="*.py" | grep -v "import\|type hint\|: MetricDefinition"

grep -rn "SCHEMA_CONTEXT\|schema_context\s*=" \
  metric_rca/services/ metric_rca/agent/ \
  --include="*.py" | grep -v "import\|def \|param\|arg"

grep -rn "_CHANNELS\|_CATEGORIES\|paid_ads\|organic\|affiliate\|electronics\|fashion\|home" \
  metric_rca/services/ metric_rca/agent/ \
  --include="*.py" | grep -v "import\|test\|#\|docstring"

grep -rn "except Exception" metric_rca/services/ metric_rca/agent/

grep -rn "read_sql\|create_engine\|pymysql\|\.execute(" \
  metric_rca/services/ metric_rca/agent/

Also run a GT leakage scan:
grep -rn "anomaly_ground_truth" metric_rca/ \
  --include="*.py" | grep -v "data/seed_data.py\|evals/\|tests/"

Expected:
- A/E grep outputs should be empty, except explicitly allowed eval/seed/test GT references.
- If any grep finds a real violation, fix it. Do not list it as a known shortcut.

────────────────────────────────────────────────────────
FINAL RESPONSE FORMAT
────────────────────────────────────────────────────────

Your final response must include:

1. Files changed
2. Tests added/updated
3. Commands run
4. Test output summary
5. Matrix rows satisfied
6. Remaining deviations by matrix row
7. Fallback-like code touched and why fail-fast
8. Actual A/E grep outputs
9. API endpoints implemented
10. Eval summary fields produced
11. Docs/screenshots status
12. Known shortcuts: []

If Known shortcuts is not exactly [], do not claim completion.
If any P0 requirement is missing, do not claim final completion.