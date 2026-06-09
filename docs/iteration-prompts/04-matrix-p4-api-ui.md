# Prompt 4 - Matrix P4: FastAPI + Streamlit

```text
You are working in MetricRCA after Matrix P3 core is complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
docs/iteration-prompts/06-review-checklist.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

GLOBAL RULES FOR THIS PHASE
- This phase is Matrix P4: API + UI only.
- Do not implement P5 eval/scoring in this phase.
- Do not change core RCA reasoning logic to satisfy API/UI tests.
- FastAPI must be a real app surface, not CLI print.
- Streamlit must be a real debug UI, not print(json).
- API must expose persisted graph outputs, not route-level hardcoded data.
- UI must read API outputs, not internal graph state.
- Local httpx calls must avoid proxy leakage with trust_env=False.
- P3B report/evidence/memory boundaries are binding:
  - final report is a verified artifact projection
  - report numeric claims must trace to persisted Evidence
  - memory may only affect drilldown priority
  - failed Reflection cannot produce report/task
  - no_anomaly cannot produce task/candidate/attribute_rank trace

P3B CONTEXT THAT MUST NOT BE BROKEN
Before coding, inspect:
- docs/reference/decisions.md ADL-0005 and ADL-0006
- metric_rca/agent/nodes/generate_report.py
- metric_rca/agent/reflection.py
- metric_rca/repositories/metric_repository.py
- metric_rca/memory/memory_repo.py
- tests/test_reflection.py
- tests/test_graph.py

ADL-0006 is binding for P4:
- P3B final report is a mechanical projection of verified artifacts.
- P4 must not return a non-persisted in-memory graph report from GET routes.
- P4 must not hardcode report/candidates/trace/evidence at the route layer.
- P4 must choose a persisted artifact strategy.

P4 REPORT STRATEGY
Use deterministic reconstruction from persisted artifacts. Do NOT add a new table
or mutate schema in P4 unless an explicit schema migration plan and tests are
added.

Required reconstruction source:
- agent_run for run metadata and status
- evidence rows, especially {run_id}:E4 result_summary.selected_candidate
- operation_task rows for created tasks
- trace_step rows for visited nodes, repair path, and errors
- sql_audit rows for SQL safety and debugging

Recommended implementation:
- Create metric_rca/reporting/projector.py
- It must expose pure functions such as:
  - build_report_from_persisted_artifacts(agent_run, evidences, tasks) -> dict | None
  - project_candidate_from_e4(e4_result_summary) -> dict | None
  - numeric_claims_from_e4(e4_result_summary, e4_id) -> list[dict]
- Refactor metric_rca/agent/nodes/generate_report.py to reuse the same projector,
  or add tests proving API reconstruction and graph report produce the same safe
  projection.
- The projected report must expose only:
  - status
  - metric_id
  - target_date
  - top_candidate identity fields only:
    root_cause_type, dimension, element, verdict
  - evidence_ids
  - numeric_claims
- It must NOT expose full RootCauseCandidate numeric fields outside numeric_claims.
- It must NOT invent causal language.
- If persisted E4 is missing or malformed, GET run should return run metadata
  plus structured error/recoverability info, not fabricate a report.

TARGET
Implement real FastAPI and Streamlit surfaces over completed graph/repository,
with API GET routes reading persisted artifacts and reconstructing report safely.

MATRIX ROWS MAPPING
- Row 21: FastAPI API
- Row 22: Streamlit debug UI
- Keep rows 1-20, 24, 26, 27 green.
- Do not claim Row 23 eval completion in this phase.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md local proxy requirement
- docs/IMPLEMENTATION_CONTRACT.md API, UI, Eval section
- docs/COMPLIANCE_MATRIX.md rows 21 and 22
- docs/MetricRCA.md sections 14, 15, 16, 19, 20
- docs/reference/decisions.md ADL-0005 and ADL-0006
- docs/MetricRCA-roadmap-checklist.md section 8.1, 8.2, phase 4
- docs/iteration-prompts/06-review-checklist.md

DEPENDENCIES
- Add FastAPI, uvicorn, httpx, streamlit with bounded compatible constraints.
- Do not add eval/scoring libraries in P4.
- Update tests/test_project_contract.py so P4 dependencies and Makefile targets
  are expected.
- Do not add LangChain prebuilt ReAct agent.
- Do not replace SQLRenderer/SQLGuard/MetricRepository with an ORM path.

SCOPE - FASTAPI

1. Implement:
   - metric_rca/api/main.py
   - metric_rca/api/routes.py
   - metric_rca/api/schemas.py
   - metric_rca/reporting/projector.py
   - optional metric_rca/api/dependencies.py

2. App requirements:
   - main.py creates a real FastAPI app.
   - routes.py contains real HTTP routes.
   - POST /api/rca/runs must call run_rca.
   - GET routes must read persisted artifacts from repositories.
   - No route can return route-level hardcoded RCA output.
   - No route can reconstruct facts from anomaly_ground_truth.
   - API response models must be typed Pydantic models.

3. Endpoints:
   - POST /api/rca/runs
     Request:
       question: str
       target_date: date | None
       business_today: date | None
       memory_enabled: bool | None
       memory_required: bool | None
     Behavior:
       invoke compiled graph through run_rca
       persist agent_run, trace_step, evidence, sql_audit, operation_task, memory as normal graph behavior
       return run_id, status, error_code, report if succeeded/no_anomaly, and links/ids for persisted artifacts
   - GET /api/rca/runs/{run_id}
     Return persisted agent_run + reconstructed verified report + candidates reconstructed from persisted E4 + task summary + error.
   - GET /api/rca/runs/{run_id}/trace
     Return persisted trace_step rows ordered by seq.
   - GET /api/rca/runs/{run_id}/evidence
     Return persisted evidence rows for run, decoded query_spec/result_summary.
   - GET /api/rca/runs/{run_id}/sql-audit
     Return persisted sql_audit rows ordered by created_at/audit_id.
   - GET /api/rca/runs/{run_id}/tasks
     Return persisted operation_task rows.
   - GET /api/rca/runs/{run_id}/memory
     Return memory hits only if they were persisted or trace-visible; do not infer memory conclusion.
   - POST /api/evals/run
     Before P5, return typed NOT_IMPLEMENTED with HTTP 501 or 409. Must not fake success.
   - GET /api/evals/{eval_id}
     Return persisted eval_run/eval_case_result if present; 404 if not found.
   - GET /health
     Return {"status": "ok"}.

4. Error response:
   Unified model:
   - error_code
   - message
   - recoverable
   - retryable
   - trace_step_id
   - suggested_next_action

   422 from Pydantic body validation must remain real FastAPI validation.
   Business failures must return structured body.

   Suggested HTTP mapping:
   - 400 for bad business request
   - 404 for missing run/eval
   - 409 for failed RCA run state or eval not implemented if using conflict semantics
   - 422 for FastAPI validation
   - 500 only for unexpected system errors that are already typed in logs
   - 501 for eval endpoint before P5 if chosen

5. Repository requirements:
   Add read methods as needed:
   - get_agent_run
   - get_trace_steps
   - get_evidences
   - get_sql_audit_rows
   - get_operation_tasks
   - get_eval_run
   - get_eval_case_results

   Reads must be parameterized.
   JSON columns must be decoded into dict/list.
   GET routes must read persisted artifacts; tests should fail if route hardcodes data.

6. Reporting projector requirements:
   - Must not query fact tables.
   - Must not call run_rca.
   - Must not read anomaly_ground_truth.
   - Must use only persisted agent_run/evidence/task/trace artifacts.
   - Must fail closed if E4 is missing/malformed for succeeded runs.
   - no_anomaly report can be reconstructed from agent_run + E1 only and must not include candidate/task.
   - failed run report is None but error body/status must be returned.

SCOPE - STREAMLIT

1. Implement:
   - metric_rca/ui/app.py
   - optional metric_rca/ui/api_client.py

2. UI requirements:
   - Real Streamlit app with 9 panels:
     a. question input
     b. conclusion/report
     c. root cause Top-K
     d. Evidence table
     e. SQL audit table
     f. Trace timeline
     g. Reflection issues
     h. Memory hits / memory status
     i. Eval summary / eval status
   - UI should read API, not import internal graph directly for normal operation.
   - Use a small API client abstraction injectable in tests.
   - For localhost httpx calls, use trust_env=False.
   - Module import must not fire network calls or start a run.
   - Eval panel before P5 must display typed NOT_IMPLEMENTED instead of fake metrics.

MAKEFILE
Update Makefile:
- api: uvicorn metric_rca.api.main:app --reload
- ui: streamlit run metric_rca/ui/app.py

Keep:
- up
- seed
- eval
- test

REQUIRED FILES
Must create/update:
- metric_rca/api/main.py
- metric_rca/api/routes.py
- metric_rca/api/schemas.py
- metric_rca/api/dependencies.py if useful
- metric_rca/reporting/projector.py
- metric_rca/ui/app.py
- metric_rca/ui/api_client.py if useful
- metric_rca/repositories/metric_repository.py read helpers
- pyproject.toml
- Makefile
- tests/test_api.py
- tests/test_ui_smoke.py
- tests/test_reporting.py
- tests/test_project_contract.py updates
- docs/reference/decisions.md if additional API/report artifact decisions are made

FORBIDDEN
- No CLI print pretending FastAPI.
- No print(json) pretending Streamlit.
- No fake endpoints.
- No route-hardcoded RCA output.
- No route-level fabricated trace/evidence/report.
- No API GET route depending on non-persisted graph return state.
- No API route bypassing run_rca on POST.
- No UI importing graph and bypassing API for normal run.
- No network call on ui/app.py import.
- No eval hardcoded success in /api/evals/run.
- No runtime code outside eval/seed/tests reading anomaly_ground_truth.
- No final report exposing unverified numeric fields.
- No memory-derived conclusion.

TDD / PROOF-TEST-FIRST
Add tests before implementation:

API tests:
1. test_health_ok
2. test_post_rca_runs_invokes_run_rca_and_persists_agent_run
3. test_get_run_reads_persisted_artifacts_not_graph_return_state
4. test_get_run_reconstructs_verified_report_from_persisted_e4
5. test_get_run_failed_state_returns_error_and_no_report
6. test_get_run_no_anomaly_has_e1_only_no_task_no_candidate
7. test_get_trace_reads_persisted_trace_ordered_by_seq
8. test_get_evidence_reads_persisted_evidence_and_decodes_json
9. test_get_sql_audit_reads_persisted_sql_audit
10. test_get_tasks_reads_persisted_operation_task
11. test_bad_body_returns_422
12. test_business_error_response_shape
13. test_eval_endpoint_does_not_fake_success_before_real_eval
14. test_api_routes_do_not_read_anomaly_ground_truth

Reporting projector tests:
15. test_projector_builds_report_from_persisted_e4_selected_candidate
16. test_projector_rejects_missing_or_malformed_e4_for_succeeded_run
17. test_projector_report_has_no_unverified_numeric_fields
18. test_projector_no_anomaly_report_has_no_candidate_or_task
19. test_projector_failed_run_has_no_report

UI tests:
20. test_ui_import_has_no_network_side_effect
21. test_ui_uses_injected_fake_api_client
22. test_ui_renders_9_required_panels_from_fake_api_data
23. test_ui_httpx_client_uses_trust_env_false
24. test_ui_eval_panel_displays_not_implemented_before_p5

Project contract tests:
25. test_makefile_api_ui_targets
26. test_pyproject_declares_p4_dependencies
27. test_no_fastapi_streamlit_import_side_effects

COMMANDS
Run:
- make seed
- pytest -q tests/test_api.py tests/test_ui_smoke.py tests/test_reporting.py
- make test
- python -W error::ResourceWarning -m unittest discover -s tests -v
Optionally run:
- make api
- make ui
Only list manual server commands as run if actually run.

ACCEPTANCE CHECKS
- FastAPI TestClient can call all documented endpoints.
- POST /api/rca/runs invokes compiled graph through run_rca.
- GET endpoints read persisted DB artifacts.
- GET /api/rca/runs/{run_id} reconstructs report from persisted evidence.
- Structured error body is uniform.
- Streamlit app has injectable API client and no import-time network.
- Makefile api/ui targets are real.
- No eval fake success.
- Known shortcuts must be exactly empty.

FINAL RESPONSE CONTRACT
Your final response must include:
1. Files changed
2. Tests added/updated
3. Commands run
4. Test output summary
5. Docs requirements satisfied, mapped to matrix rows
6. Remaining deviations, mapped to matrix rows
7. Fallback-like code touched and why it is still fail-fast
8. Known shortcuts: []
If Known shortcuts is not exactly [], do not claim completion.
```
