# Prompt 3 - Matrix P3 Continuation: Full Reflection + Memory

```text
You are working in MetricRCA after Matrix P2 and Matrix P3 Part A are complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

IMPORTANT PHASE NAMING
This conversation may call this "Phase 4 Reflection + Memory", but in
docs/COMPLIANCE_MATRIX.md it is Matrix P3 continuation, not Matrix P4. Do not
start API/UI work here.

GLOBAL RULES FOR THIS PHASE
- Do not optimize for green tests by simplifying architecture.
- Preserve QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan
  for metric facts.
- Reflection is a deterministic rule verifier, not a summary, not an LLM
  self-critique stub.
- Memory may influence drilldown priority only. It must never become final
  conclusion.
- Failed Reflection must not generate report.
- Repair must return through legal ReAct/tool/query path.

TARGET
Complete Matrix P3 rows 19 and 20, and strengthen row 24 zero-fallback
negatives:
- Full deterministic Reflection verifier
- Repair path through AgentAction -> execute_tool -> QuerySpec -> Renderer ->
  Guard -> Repository -> new Evidence -> verify again
- Real memory repository
- read_memory/write_memory nodes wired to memory_repo
- Memory failure and pollution tests

MATRIX ROWS MAPPING
- Row 19: Reflection verifier & repair
- Row 20: Memory repository
- Row 24 continuation: zero-fallback negative tests for memory/reflection
- Row 16 continuation: current-run evidence binding and report traceability
- Keep rows 1-18 and Phase 3 Part A graph tests green.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md
- docs/IMPLEMENTATION_CONTRACT.md Reflection and Memory sections
- docs/COMPLIANCE_MATRIX.md rows 16, 19, 20, 24
- docs/MetricRCA.md sections 7, 8, 15, 18
- docs/MetricRCA-roadmap-checklist.md sections 6, 7, 12

SCOPE - REFLECTION
1. Complete metric_rca/agent/reflection.py:
   - Deterministic rule verifier.
   - No LLM dependency.
   - Must return typed ReflectionResult with issues.

2. Required checks:
   - required_evidence_present:
     confirmed/likely candidate must bind current-run E1-E4 unless
     status=no_anomaly.
   - current_run_evidence:
     evidence_ids must exist in state.evidences and persisted evidence for same
     run_id.
   - guard_status_passed:
     every evidence guard_status must be passed.
   - numeric_traceability:
     every numeric claim in report must appear in some Evidence.result_summary.
   - time_range_consistency:
     evidence query_spec time_range and result_summary dates match
     target/baseline.
   - metric_consistency:
     evidence metric_id matches parsed metric and candidate/report metric.
   - attribution_coverage:
     top contribution must meet configured threshold; else
     ATTRIBUTION_COVERAGE_LOW.
   - no_anomaly_task_behavior:
     no_anomaly cannot create operation_task and cannot include confirmed root
     cause.
   - causal_language:
     insufficient evidence cannot produce confirmed causal language such as
     "caused by" / "导致".
   - repair_limit:
     repair_count <= settings.max_repair.

3. Repair path:
   - ReflectionIssue(severity="error", suggested_action=AgentAction(...)) routes
     to react_step.
   - The suggested action must pass validate_action.
   - execute_tool must run the legal tool.
   - Tool must create QuerySpec, render SQL, pass SQLGuard, execute Repository,
     persist new Evidence and sql_audit.
   - reflection_verify runs again after repair.
   - If still failing or repair_count > max_repair:
     REFLECTION_REPAIR_FAILED -> error_return.
   - A test must fail if implementation merely increments repair_count and marks
     passed.

4. Report gating:
   - generate_report cannot run after failed Reflection.
   - report may be generated only when ReflectionResult.passed=True or
     status=no_anomaly.
   - If reflection fails after repair, final state.status="failed",
     error_code="REFLECTION_REPAIR_FAILED", report is None.

SCOPE - MEMORY
1. Implement real memory repo:
   - File: metric_rca/memory/memory_repo.py
   - Not a re-export shell.
   - Uses system table memory_record.
   - It may use application repository/engine for system table reads/writes, but
     it must not use QuerySpec because memory_record is not metric fact data.
   - It must use parameterized SQLAlchemy text queries; no string interpolation
     for values.

2. Memory record contract:
   - External contract field should be mem_key, matching DB column.
   - If keeping domain MemoryRecord.key internally, add explicit alias/mapper and
     tests.
   - Required fields:
     layer
     mem_key
     payload
     confidence
     source
     version
     ttl_days
     created_at
   - Exact key example: gmv|channel.

3. Read behavior:
   - read by exact (layer, mem_key)
   - ignore expired records using ttl_days
   - ignore low-confidence memory below threshold
   - if multiple versions match, higher version wins
   - memory_enabled=false -> do not call repo
   - memory_required=true and read fails -> MEMORY_READ_FAILED -> error_return
   - memory_required=false and read fails -> typed observation/trace warning, but
     must not affect conclusion

4. Write behavior:
   - write structured case/session memory only; no large chat transcript blobs
   - include confidence/source/version/ttl_days
   - memory_required=true and write fails -> MEMORY_WRITE_FAILED -> error_return
   - memory_enabled=false -> do not call repo

5. Planning influence:
   - memory hit can reorder drilldown priority only.
   - memory cannot create candidate.
   - memory cannot change verdict to confirmed/likely.
   - memory cannot be used as evidence_id.
   - reflection_factor boost must be <= 1.2 and only applies when independent
     current-run evidence exists.

REQUIRED FILES
Must create/update:
- metric_rca/agent/reflection.py
- metric_rca/agent/nodes/reflection_verify.py
- metric_rca/agent/nodes/read_memory.py
- metric_rca/agent/nodes/write_memory.py
- metric_rca/memory/memory_repo.py
- metric_rca/domain/models.py if needed for mem_key alignment
- metric_rca/repositories/metric_repository.py only if system table read helpers
  are needed
- tests/test_reflection.py
- tests/test_memory.py
- tests/test_zero_fallback.py additions
- tests/test_graph.py additions for no_anomaly/no task regression

FORBIDDEN
- No Reflection stub or unconditional passed=True.
- No final report after failed Reflection.
- No repair bypassing ReAct/tool/query path.
- No Memory-derived conclusion.
- No memory evidence_id.
- No low-confidence/expired/low-version memory influence.
- No broad except Exception: continue.
- No swallowing memory_required failures.
- No re-export-only memory_repo.
- No API/UI/eval work in this phase.

TDD / PROOF-TEST-FIRST
Add tests before implementation:
1. test_reflection_missing_evidence_fails_no_report
2. test_reflection_candidate_without_current_run_evidence_fails
3. test_reflection_guard_status_not_passed_fails
4. test_reflection_untraceable_numeric_claim_fails
5. test_reflection_time_range_mismatch_fails
6. test_reflection_metric_mismatch_fails
7. test_reflection_low_attribution_coverage_returns_ATTRIBUTION_COVERAGE_LOW
8. test_reflection_no_anomaly_cannot_have_operation_task
9. test_reflection_insufficient_evidence_blocks_confirmed_causal_language
10. test_reflection_repair_suggested_action_reenters_react_tool_guard_repo_and_generates_new_evidence
11. test_reflection_repair_count_increment_without_new_evidence_does_not_pass
12. test_reflection_repair_failed_routes_error_return_no_report
13. test_memory_repo_is_real_not_reexport_shell
14. test_memory_exact_key_hit_reorders_drilldown_only
15. test_memory_low_confidence_ignored
16. test_memory_expired_ignored
17. test_memory_version_conflict_higher_version_wins
18. test_memory_required_read_failure_fails_run
19. test_memory_required_write_failure_fails_run
20. test_memory_enabled_false_does_not_call_repo
21. test_memory_cannot_be_final_conclusion_without_current_evidence

COMMANDS
Run:
- make seed
- pytest -q tests/test_reflection.py tests/test_memory.py tests/test_zero_fallback.py tests/test_graph.py
- make test
- python -W error::ResourceWarning -m unittest discover -s tests -v

ACCEPTANCE CHECKS
- Reflection verifier is deterministic and rule-based.
- Repair produces a real new Evidence row and sql_audit row.
- Failed Reflection never generates report.
- Memory repo reads/writes memory_record and is not alias/re-export.
- Memory hit changes drilldown priority only; conclusion still requires
  current-run Evidence.
- All 8 zero-fallback named tests are present or extended.
- Matrix P3 can be claimed complete only if rows 11,13,14,15,16,19,20,24 are
  green.
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
