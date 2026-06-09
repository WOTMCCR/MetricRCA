# Prompt 5 - Matrix P5: Eval + README + Final Compliance

```text
You are working in MetricRCA after Matrix P2, P3, and P4 are complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
docs/iteration-prompts/06-review-checklist.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

GLOBAL RULES FOR THIS PHASE
- This phase is Matrix P5: eval + docs + final compliance.
- Do not hardcode eval success.
- Do not echo ground truth as runtime RCA output.
- Eval must run RCA on each case and compare persisted outputs to anomaly_ground_truth.
- Eval must score persisted artifacts, not only in-memory graph return state.
- dangerous_sql_blocked must be a real boolean derived from an actual SQLGuard negative check.
- README must not claim features beyond implementation.
- If a requirement is missing, list it as a deviation; do not claim compliance.
- P3B/P4 boundaries remain binding:
  - report numeric claims must trace to persisted Evidence
  - final report must be a verified projection, not free text
  - memory cannot become evidence or conclusion
  - no_anomaly cannot create task/candidate/attribute_rank trace
  - failed Reflection cannot generate report/task

TARGET
Implement real eval runner/scorer and final documentation/compliance artifacts.

MATRIX ROWS MAPPING
- Row 23: Eval runner / scorer
- Row 25: README & architecture docs
- Row 24 finalization: zero-fallback negative tests complete
- Keep rows 1-22, 26, 27 green.
- No P0 missing at final claim.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md
- docs/IMPLEMENTATION_CONTRACT.md
- docs/COMPLIANCE_MATRIX.md rows 23, 24, 25
- docs/MetricRCA.md sections 10, 14, 15, 16, 17, 18, 19, 20
- docs/reference/decisions.md ADL-0005 and ADL-0006
- docs/MetricRCA-roadmap-checklist.md sections 8.3, 9 phase 5, 12
- current P4 API/reporting code:
  - metric_rca/reporting/projector.py
  - metric_rca/api/routes.py
  - metric_rca/repositories/metric_repository.py read helpers

SCOPE - EVAL

1. Implement:
   - metric_rca/evals/cases.jsonl
   - metric_rca/evals/runner.py
   - metric_rca/evals/scorer.py
   - optional metric_rca/evals/models.py

2. Cases:
   - cases.jsonl must contain the five MVP cases:
     gmv_paid_ads_drop
     gmv_stockout_electronics
     cvr_mobile_drop
     refund_rate_product_quality
     gmv_no_anomaly
   - Each case must include case_id and question.
   - Expected fields must be loaded from anomaly_ground_truth or validated against it.
   - cases.jsonl may contain question and metadata, but must not be the scoring source of truth.
   - If cases.jsonl and anomaly_ground_truth disagree, fail typed.
   - If any GT row is missing:
     EVAL_GROUND_TRUTH_MISSING.

3. Runner:
   - Load cases.
   - Read anomaly_ground_truth from DB.
   - For each case, call run_rca exactly once unless the case fails before run due to missing GT.
   - Use deterministic run_id derived from eval_id + case_id or an explicit safe prefix.
   - Persist eval_run.
   - Persist eval_case_result for each case.
   - Score from persisted artifacts:
     - agent_run
     - evidence
     - trace_step
     - operation_task
     - sql_audit
     - reconstructed verified report from reporting projector
   - Output JSON and Markdown.
   - Exit code 0 only when eval completes successfully and thresholds are met.
   - Exit nonzero with structured error for missing GT or critical eval failure.

4. Scorer:
   Per-case fields:
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

5. Scoring rules:
   - intent_ok:
     persisted agent_run.metric_id == GT metric_id.
   - anomaly_ok:
     if GT expected_anomaly=false, final status must be no_anomaly.
     if GT expected_anomaly=true, final status must be succeeded and E1 must indicate anomaly.
   - top1_ok:
     reconstructed report/candidate top1 root_cause_type, dimension, element match GT.
   - top3_ok:
     any persisted candidate list from E4.result_summary.candidates matches GT.
   - evidence_coverage:
     confirmed/likely top candidate has current-run E1-E4, each persisted and guard_status=passed.
   - sql_safe:
     all sql_audit guard_status values are passed/rejected as expected and no executed unsafe SQL appears.
   - reflection_repair_ok:
     if repair occurred, trace shows reflection_verify -> react_step -> execute_tool -> reflection_verify and new Evidence/sql_audit exists.
   - report_traceable_ok:
     every numeric claim in reconstructed report maps to persisted Evidence result_summary.
   - memory_pollution_ok:
     memory_hits, if present, do not appear as evidence_ids and cannot create candidate without current-run Evidence.
   - no_anomaly_correct:
     status=no_anomaly, exactly E1 evidence, no operation_task, no attribute_rank trace, no confirmed candidate.

6. dangerous_sql_blocked:
   - Must call the real SQLGuard on at least one dangerous SQL, e.g. DELETE, SELECT *, multi-statement, or derived table bypass attempt.
   - Must be True only if guard rejects.
   - Must be bool, never null.
   - Add a test that monkeypatches/breaks guard behavior and proves dangerous_sql_blocked changes; this prevents constant True.

7. GT proof tests:
   - Mutate one GT row and assert scorer output changes.
   - Delete one GT row and assert EVAL_GROUND_TRUTH_MISSING.
   - Do not score by reading expected_root_cause from cases.jsonl alone.
   - Runtime services/tools/agent/api/reporting must not read anomaly_ground_truth.

8. API integration:
   - If P4 API exists, eval runner may call run_rca directly for speed, but scoring must use the same persisted-artifact reconstruction path as API GET.
   - Add a test that scorer output is the same whether report is reconstructed through reporting projector or API GET payload.

SCOPE - DOCS

1. README.md:
   Must accurately document:
   - Architecture
   - QuerySpec -> SQLRenderer -> SQLGuard -> Repository sole data path
   - LangGraph/ReAct
   - Reflection repair path
   - Memory boundary
   - Final report verified projection
   - API persisted artifact contract
   - Zero Silent Fallback
   - make up / seed / api / ui / eval / test
   - API endpoints
   - Eval metrics
   - Error codes
   - Target response example
   - Known limitations/deviations

2. docs/architecture.md:
   Add Mermaid diagrams:
   a. logical architecture
   b. graph control flow
   c. ReAct repair path
   d. QuerySpec data path
   e. persisted artifact/report reconstruction path
   f. memory pollution boundary
   g. eval pipeline

   Diagrams must match actual implementation.

3. screenshots/:
   - Add real screenshots if environment can run UI.
   - Do not create fake screenshot placeholders.
   - If screenshots cannot be captured in the environment, create screenshots/README.md
     explaining exact command to reproduce and list this as a remaining non-P0
     deviation. Do not claim screenshot completed.

4. Final compliance matrix:
   - Update docs/COMPLIANCE_MATRIX.md status or add docs/final-compliance.md.
   - For each row 1-27:
     status must be one of:
       satisfied
       partial
       intentionally deferred
       missing
     Include proof command/test.
   - No P0 missing or partial may be claimed complete.
   - Any deviation must be explicit.
   - If bounded SQL retry remains deferred, list it as a known non-P0 deviation
     only if project owner accepts fail-fast typed SQL_EXECUTION_FAILED in its place.

REQUIRED FILES
Must create/update:
- metric_rca/evals/cases.jsonl
- metric_rca/evals/runner.py
- metric_rca/evals/scorer.py
- metric_rca/evals/models.py if useful
- metric_rca/repositories/metric_repository.py eval read/write helpers if needed
- README.md
- docs/architecture.md
- docs/final-compliance.md or update docs/COMPLIANCE_MATRIX.md with status/proof
- screenshots/ real files or screenshots/README.md
- tests/test_eval.py
- tests/test_zero_fallback.py final additions
- tests/test_docs_compliance.py
- tests/test_project_contract.py updates if needed

FORBIDDEN
- No hardcoded eval success.
- No dangerous_sql_blocked = null.
- No dangerous_sql_blocked = True constant.
- No scoring from cases.jsonl expected answers without DB GT validation.
- No README claims exceeding implementation.
- No fake screenshots.
- No no_anomaly task.
- No bypassing run_rca during eval.
- No scorer using graph in-memory state instead of persisted artifacts.
- No deleting/weakening zero-fallback tests.
- No broad except Exception returning successful eval.
- No GT leakage into runtime services/agent/api/reporting code.
- No memory-derived conclusion.
- No final report numeric claim without persisted Evidence.
- No eval route faking success.

TDD / PROOF-TEST-FIRST
Add tests before implementation:
1. test_eval_loads_cases_and_ground_truth
2. test_eval_missing_ground_truth_returns_EVAL_GROUND_TRUTH_MISSING
3. test_eval_mutating_ground_truth_changes_score
4. test_eval_runs_rca_for_each_case
5. test_eval_scores_from_persisted_artifacts_not_graph_return_state
6. test_eval_writes_eval_run_and_eval_case_result
7. test_eval_scores_intent_anomaly_top1_top3_evidence_sql_reflection
8. test_eval_report_traceable_ok_requires_persisted_numeric_claims
9. test_eval_memory_pollution_ok_rejects_memory_evidence_id
10. test_dangerous_sql_blocked_is_real_boolean_from_guard
11. test_dangerous_sql_blocked_not_constant_when_guard_monkeypatched
12. test_no_anomaly_correct_requires_no_task_no_attribute_rank_no_candidate
13. test_eval_json_and_markdown_outputs_exist
14. test_runtime_code_outside_seed_eval_tests_does_not_read_anomaly_ground_truth
15. test_readme_commands_match_makefile
16. test_readme_endpoints_match_fastapi_routes
17. test_readme_error_codes_match_domain_or_api_error_models
18. test_architecture_md_has_mermaid_and_matches_required_nodes
19. test_architecture_md_mentions_persisted_report_projection
20. test_final_compliance_has_rows_1_to_27_with_status_and_proof
21. Ensure all zero-fallback named tests exist and pass:
    - test_llm_required_unavailable_fails
    - test_illegal_action_records_error_and_does_not_execute_tool
    - test_memory_required_read_failure_fails_run
    - test_memory_required_write_failure_fails_run
    - test_empty_result_does_not_enter_attribute_rank
    - test_sql_execution_failure_routes_error_return
    - test_sql_guard_rejection_cannot_bypass_renderer
    - test_no_anomaly_skips_create_tasks
    - test_failed_reflection_cannot_generate_report_or_task

COMMANDS
Run:
- make up
- make seed
- make eval
- make test
- python -W error::ResourceWarning -m unittest discover -s tests -v

ACCEPTANCE CHECKS
- make eval no longer prints NOT IMPLEMENTED.
- make eval writes eval_run and eval_case_result.
- Summary includes case_total, dangerous_sql_blocked, no_anomaly_correct, report_traceable_rate.
- dangerous_sql_blocked is boolean true from real SQLGuard rejection.
- Deleting GT row fails with EVAL_GROUND_TRUTH_MISSING.
- Mutating GT changes score.
- gmv_no_anomaly has status=no_anomaly, no operation_task, no attribute_rank trace.
- Eval scores persisted artifacts, not graph return state.
- README commands/endpoints/error codes match actual files.
- Final compliance says no P0 missing.
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
