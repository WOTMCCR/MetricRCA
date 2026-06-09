# Prompt 4 - Matrix P4: FastAPI + Streamlit

```text
You are working in MetricRCA after Matrix P3 core is complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

GLOBAL RULES FOR THIS PHASE
- This phase is Matrix P4: API + UI only.
- Do not change core RCA logic to satisfy API/UI tests.
- FastAPI must be a real app surface, not CLI print.
- Streamlit must be a real debug UI, not print(json).
- API must expose persisted graph outputs, not route-level hardcoded data.
- Local httpx calls must avoid proxy leakage with trust_env=False.

TARGET
Implement real FastAPI and Streamlit surfaces over the completed graph/repository.

MATRIX ROWS MAPPING
- Row 21: FastAPI API
- Row 22: Streamlit debug UI
- Keep rows 1-20 and 24 green.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md local proxy requirement
- docs/IMPLEMENTATION_CONTRACT.md API, UI, Eval section
- docs/COMPLIANCE_MATRIX.md rows 21 and 22
- docs/MetricRCA.md sections 14, 15, 16, 19, 20
- docs/MetricRCA-roadmap-checklist.md section 8.1, 8.2, phase 4

DEPENDENCIES
- Add FastAPI, uvicorn, httpx, streamlit with bounded compatible constraints.
- Update tests/test_project_contract.py so these dependencies and Makefile
  targets are expected in Matrix P4.
- Do not add eval scoring shortcuts.

SCOPE - FASTAPI
1. Implement:
   - metric_rca/api/main.py
   - metric_rca/api/routes.py
   - Optional metric_rca/api/schemas.py for request/response/error models

2. App requirements:
   - main.py creates FastAPI app.
   - routes.py contains real HTTP routes.
   - Routes must use run_rca and repositories.
   - Routes must return persisted artifacts.

3. Endpoints:
   - POST /api/rca/runs
     Request: question, optional target_date
     Behavior: create run, synchronously invoke compiled graph through run_rca,
     persist artifacts, return run_id and status.
   - GET /api/rca/runs/{run_id}
     Return agent_run, report, candidates, error if any.
   - GET /api/rca/runs/{run_id}/trace
     Return persisted trace_step rows ordered by seq.
   - GET /api/rca/runs/{run_id}/evidence
     Return persisted evidence rows for run.
   - POST /api/evals/run
     May call eval runner if implemented; before Matrix P5 it may return a typed
     NOT_IMPLEMENTED or current runner status, but must not fake success.
   - GET /api/evals/{eval_id}
     Return persisted eval_run and eval_case_result if present; 404 if not found.
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

5. Repository requirements:
   - Add read methods as needed:
     get_agent_run
     get_trace_steps
     get_evidences
     get_operation_tasks
     get_eval_run
     get_eval_case_results
   - Reads must be parameterized.
   - GET routes must read persisted artifacts; tests should fail if route
     hardcodes data.

SCOPE - STREAMLIT
1. Implement:
   - metric_rca/ui/app.py

2. UI requirements:
   - Real Streamlit app with 9 panels:
     a. question input
     b. conclusion/report
     c. root cause Top-K
     d. Evidence table
     e. SQL audit table
     f. Trace timeline
     g. Reflection issues
     h. Memory hits
     i. Eval summary
   - UI should read API, not import internal graph directly for normal operation.
   - Use a small API client abstraction that can be injected in tests.
   - For localhost httpx calls, use trust_env=False.
   - Module import must not fire network calls or start a run.

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
- metric_rca/api/schemas.py if useful
- metric_rca/ui/app.py
- metric_rca/repositories/metric_repository.py read helpers if needed
- pyproject.toml
- Makefile
- tests/test_api.py
- tests/test_ui_smoke.py
- tests/test_project_contract.py updates

FORBIDDEN
- No CLI print pretending FastAPI.
- No print(json) pretending Streamlit.
- No fake endpoints.
- No route-hardcoded RCA output.
- No route-level fabricated trace/evidence.
- No API route bypassing run_rca.
- No UI importing graph and bypassing API for normal run.
- No network call on ui/app.py import.
- No eval hardcoded success in /api/evals/run.
- No broad except Exception returning fake success.

TDD / PROOF-TEST-FIRST
Add tests before implementation:
1. test_health_ok
2. test_post_rca_runs_invokes_run_rca_and_persists_agent_run
3. test_get_run_reads_persisted_report_and_candidates
4. test_get_trace_reads_persisted_trace_ordered_by_seq
5. test_get_evidence_reads_persisted_evidence
6. test_bad_body_returns_422
7. test_business_error_response_shape
8. test_eval_endpoint_does_not_fake_success_before_real_eval
9. test_makefile_api_ui_targets
10. test_ui_import_has_no_network_side_effect
11. test_ui_uses_injected_fake_api_client
12. test_ui_renders_9_required_panels_from_fake_api_data
13. test_ui_httpx_client_uses_trust_env_false

COMMANDS
Run:
- make seed
- pytest -q tests/test_api.py tests/test_ui_smoke.py
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
