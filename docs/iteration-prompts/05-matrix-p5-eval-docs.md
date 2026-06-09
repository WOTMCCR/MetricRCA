# Prompt 5 - Matrix P5: Eval + README + Final Compliance

```text
You are working in MetricRCA after Matrix P2, P3, and P4 are complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

GLOBAL RULES FOR THIS PHASE
- This phase is Matrix P5: eval + docs + final compliance.
- Do not hardcode eval success.
- Do not echo ground truth as runtime RCA output.
- Eval must run RCA on each case and compare outputs to anomaly_ground_truth.
- dangerous_sql_blocked must be a real boolean derived from an actual SQLGuard
  negative check.
- README must not claim features beyond implementation.
- If a requirement is missing, list it as a deviation; do not claim compliance.

TARGET
Implement real eval runner/scorer and final documentation/compliance artifacts.

MATRIX ROWS MAPPING
- Row 23: Eval runner / scorer
- Row 25: README & architecture docs
- Row 24 finalization: zero-fallback negative tests complete
- Keep rows 1-22 green.
- No P0 missing at final claim.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md
- docs/IMPLEMENTATION_CONTRACT.md
- docs/COMPLIANCE_MATRIX.md rows 23, 24, 25
- docs/MetricRCA.md sections 10, 14, 15, 17, 18, 19, 20
- docs/MetricRCA-roadmap-checklist.md sections 8.3, 9 phase 5, 12

SCOPE - EVAL
1. Implement:
   - metric_rca/evals/cases.jsonl
   - metric_rca/evals/runner.py
   - metric_rca/evals/scorer.py
   - Optional metric_rca/evals/models.py

2. Cases:
   - cases.jsonl must contain the five MVP cases:
     gmv_paid_ads_drop
     gmv_stockout_electronics
     cvr_mobile_drop
     refund_rate_product_quality
     gmv_no_anomaly
   - Each case must include case_id and question.
   - Expected fields must be loaded from anomaly_ground_truth or validated
     against it.
   - If cases.jsonl and anomaly_ground_truth disagree, fail typed.
   - If any GT row is missing:
     EVAL_GROUND_TRUTH_MISSING.

3. Runner:
   - Load cases.
   - Read anomaly_ground_truth from DB.
   - Run run_rca for each case.
   - Persist eval_run.
   - Persist eval_case_result for each case.
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

   Summary fields:
   - case_total
   - top1_rate
   - top3_rate
   - anomaly_accuracy
   - evidence_coverage_avg
   - sql_safe_rate
   - dangerous_sql_blocked
   - no_anomaly_correct

5. dangerous_sql_blocked:
   - Must call the real SQLGuard on at least one dangerous SQL, e.g. DELETE or
     SELECT * or multi-statement.
   - Must be True only if guard rejects.
   - Must be bool, never null.
   - Add a test that monkeypatches/breaks guard behavior and proves
     dangerous_sql_blocked changes; this prevents constant True.

6. no_anomaly_correct:
   - Must check:
     a. status == no_anomaly
     b. no operation_task created
     c. no attribute_rank trace
     d. no confirmed root cause candidate
   - Must fail if a task exists.

7. GT proof tests:
   - Mutate one GT row and assert scorer output changes.
   - Delete one GT row and assert EVAL_GROUND_TRUTH_MISSING.
   - Do not score by reading expected_root_cause from cases.jsonl alone.

SCOPE - DOCS
1. README.md:
   Must accurately document:
   - Architecture
   - QuerySpec -> SQLRenderer -> SQLGuard -> Repository sole data path
   - LangGraph/ReAct
   - Reflection
   - Memory boundary
   - Zero Silent Fallback
   - make up / seed / api / ui / eval / test
   - API endpoints
   - Eval metrics
   - Error codes
   - Target response example
   - Known limitations/deviations

2. docs/architecture.md:
   - Add Mermaid diagrams:
     a. logical architecture
     b. graph control flow
     c. QuerySpec data path
     d. eval pipeline
   - Must match actual implementation.

3. screenshots/:
   - Add real screenshots if environment can run UI.
   - Do not create fake screenshot placeholders.
   - If screenshots cannot be captured in the environment, create
     screenshots/README.md explaining exact command to reproduce and list this
     as a remaining non-P0 deviation. Do not claim screenshot completed.

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
- No deleting/weakening zero-fallback tests.
- No broad except Exception returning successful eval.
- No GT leakage into runtime services/agent code.

TDD / PROOF-TEST-FIRST
Add tests before implementation:
1. test_eval_loads_cases_and_ground_truth
2. test_eval_missing_ground_truth_returns_EVAL_GROUND_TRUTH_MISSING
3. test_eval_mutating_ground_truth_changes_score
4. test_eval_runs_rca_for_each_case
5. test_eval_writes_eval_run_and_eval_case_result
6. test_eval_scores_intent_anomaly_top1_top3_evidence_sql_reflection
7. test_dangerous_sql_blocked_is_real_boolean_from_guard
8. test_dangerous_sql_blocked_not_constant_when_guard_monkeypatched
9. test_no_anomaly_correct_requires_no_task_no_attribute_rank_no_candidate
10. test_eval_json_and_markdown_outputs_exist
11. test_runtime_code_outside_seed_eval_tests_does_not_read_anomaly_ground_truth
12. test_readme_commands_match_makefile
13. test_readme_endpoints_match_fastapi_routes
14. test_readme_error_codes_match_domain_or_api_error_models
15. test_architecture_md_has_mermaid_and_matches_required_nodes
16. test_final_compliance_has_rows_1_to_27_with_status_and_proof
17. Ensure all 8 zero-fallback named tests exist and pass:
    - test_llm_required_unavailable_fails
    - test_illegal_action_records_error_and_does_not_execute_tool
    - test_memory_required_read_failure_fails_run
    - test_memory_required_write_failure_fails_run
    - test_empty_result_does_not_enter_attribute_rank
    - test_sql_execution_retry_exhausted_fails_run
    - test_sql_guard_rejection_cannot_bypass_renderer
    - test_no_anomaly_skips_create_tasks

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
- Summary includes case_total, dangerous_sql_blocked, no_anomaly_correct.
- dangerous_sql_blocked is boolean true from real SQLGuard rejection.
- Deleting GT row fails with EVAL_GROUND_TRUTH_MISSING.
- Mutating GT changes score.
- gmv_no_anomaly has status=no_anomaly, no operation_task, no attribute_rank
  trace.
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
