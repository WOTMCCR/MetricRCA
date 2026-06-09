# Prompt 1 - Matrix P2: Services + Tools + Evidence Emission

```text
You are working in the MetricRCA repository.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md
If this prompt is pasted into another Codex/Goal session, paste the full global
rules file above this phase prompt. The local rules below are additions and
phase-specific constraints, not a replacement.

GLOBAL RULES FOR THIS PHASE
- Do not optimize for green tests by simplifying architecture.
- Source of truth priority:
  1. Current user instruction
  2. AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md
  3. docs/MetricRCA.md
  4. docs/MetricRCA-roadmap-checklist.md
  5. docs/COMPLIANCE_MATRIX.md as the row-by-row executable gate
- Preserve the only metric-fact data path:
  QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan.
- Do not introduce raw SQL execution in services or tools.
- Do not read anomaly_ground_truth from runtime services/tools/agent code.
- Do not implement LangGraph, API, UI, or real eval scoring in this phase.
- Passing tests is not enough if the implementation violates docs.

TARGET
Implement Matrix P2: services + deterministic tool layer + evidence emission.
This phase must make the algorithm and tool layer real, but must not fake an
agent graph.

MATRIX ROWS MAPPING
- Row 12: Tool layer
- Row 16 partial: Evidence E1-E4 emission and persistence from tools
- Row 17: Anomaly detection
- Row 18: Attribution + GMV = UV * PAY_CVR * AOV
- Row 27 partial: Typed tool/service contracts for P2 data tools
- Keep rows 1-10 and 26 green.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md
- docs/IMPLEMENTATION_CONTRACT.md
- docs/COMPLIANCE_MATRIX.md rows 12, 16, 17, 18, 27
- docs/MetricRCA.md sections 1, 2, 10, 11, 12, 13, 15, 18
- docs/MetricRCA-roadmap-checklist.md sections 0, 2.3, 3, 4, 8.3, 9 phase 2, 12

CODEX-LOCAL HARDENING
- E1/E2/E3/E4 are per-run evidence aliases, not global primary keys.
  Persisted evidence_id must be globally unique, e.g. "{run_id}:E1".
  Observation.evidence_ids must contain persisted evidence_id values.
  ToolResult may expose evidence_alias for E1/E2/E3/E4 semantics.
- Use one QuerySpec(purpose="baseline") for exact same-weekday baseline.
  SQLRenderer must render business_date IN (:baseline_d0,:baseline_d1,
  :baseline_d2,:baseline_d3), with target_date-7, -14, -21, -28. Broad BETWEEN
  baseline is forbidden.
- services/anomaly_service.py and services/attribution_service.py are pure
  deterministic computation over typed inputs/rows. They must not import
  MetricRepository, SQLRenderer, SQLGuard, SQLAlchemy, pymysql, pandas.read_sql,
  or create_engine.
- Direction is derived from MetricDefinition.higher_is_better:
  higher_is_better=True means bad direction is current < baseline.
  higher_is_better=False means bad direction is current > baseline, e.g.
  refund_rate.

SCOPE
1. Implement metric service:
   - File: metric_rca/services/metric_service.py
   - Provide typed parsed intent model, either in this file or in
     domain/models.py.
   - Parse only the six MVP question families:
     a. yesterday GMV drop
     b. yesterday net GMV drop
     c. yesterday pay conversion rate drop
     d. yesterday refund rate increase
     e. yesterday channel GMV anomaly
     f. yesterday category GMV anomaly
   - Return typed errors:
     METRIC_NOT_FOUND
     DIMENSION_NOT_ALLOWED
     DATE_RANGE_INVALID
     PARSE_FAILED
   - Unsupported metric, unsupported dimension, and unsupported date must be
     separate tests.
   - Do not parse arbitrary Text-to-SQL.
   - Do not read anomaly_ground_truth.

2. Fix baseline data access:
   - Baseline is exactly previous 4 same weekdays: t-7, t-14, t-21, t-28.
   - SQLRenderer must deterministically render a baseline `IN` query for
     QuerySpec(purpose="baseline").
   - Update renderer and guard tests. Guard already supports IN; do not weaken
     it.
   - Add a proof test that fails if implementation uses a broad BETWEEN range.

3. Implement anomaly service:
   - File: metric_rca/services/anomaly_service.py
   - Baseline:
     baseline_dates = [target-7, target-14, target-21, target-28]
     baseline_mean = mean(values)
     baseline_std = std(values)
     delta = current - baseline_mean
     delta_pct = delta / baseline_mean
     z_score = delta / max(baseline_std, eps)
     is_anomaly = abs(delta_pct) >= settings.thresh_pct and
       abs(z_score) >= settings.z_thresh, plus bad-direction classification from
       higher_is_better.
   - sample_n < 3 -> INSUFFICIENT_BASELINE_DATA.
   - No anomaly -> NO_ANOMALY_DETECTED as explicit success observation, not a
     failure.
   - No empty-data continuation.

4. Implement attribution service:
   - File: metric_rca/services/attribution_service.py
   - Dimension contribution:
     For higher_is_better metrics: bad_delta_by_dim = max(0, baseline - current)
     For higher_is_better=False metrics: bad_delta_by_dim = max(0, current - baseline)
     contribution_pct = bad_delta_by_dim / sum(bad_delta_by_dim)
   - If rows are empty or total bad_delta is zero, return
     ATTRIBUTION_COVERAGE_LOW or typed insufficient observation. Do not
     fabricate candidates.
   - GMV decomposition:
     GMV = UV * PAY_CVR * AOV
     PAY_CVR = pay_user_cnt / uv
     AOV = gmv / pay_user_cnt
     Do not use pay_orders.
   - net_gmv = gmv - refund.
   - RootCauseCandidate ranking must use engineering confidence only:
     score = contribution_score * signal_severity * evidence_support *
       reflection_factor
     eng_confidence = normalized engineering score, not statistical confidence.
   - Implement top contribution threshold. Low coverage must be typed error
     ATTRIBUTION_COVERAGE_LOW.

5. Implement real tool files:
   - metric_rca/agent/tools/detect_anomaly.py
   - metric_rca/agent/tools/drilldown_dimension.py
   - metric_rca/agent/tools/fetch_related_signal.py
   - metric_rca/agent/tools/calculate_contribution.py
   - Shared typed schemas may be placed in metric_rca/agent/tools/schemas.py,
     but behavior must remain in the real tool modules.
   - detect_anomaly emits E1 for metric current vs exact same-weekday baseline.
   - drilldown_dimension emits E2 for dimension contribution candidates.
   - fetch_related_signal emits E3; it must support campaign signal for
     paid_ads, inventory stockout for category/product, traffic conversion
     signal for device/channel, and customer ticket/refund quality signal for
     refund_rate/product/category where applicable.
   - calculate_contribution emits E4; it must compute final
     contribution/decomposition from current-run E1-E3 evidence and fresh
     guarded queries where needed.
   - Each tool must:
     a. accept typed Pydantic args with extra="forbid"
     b. accept run_id and require a valid current run
     c. create QuerySpec via build_query_spec
     d. call SQLRenderer
     e. call SQLGuard
     f. call MetricRepository.execute_plan only with guard_status="passed"
     g. persist evidence using repository.create_evidence
     h. rely on MetricRepository.execute_plan to write sql_audit
     i. return a typed ToolResult containing Observation plus Evidence objects
     j. return Observation(ok=False, error_code=...) for typed tool errors
   - Tools must not execute SQL directly through SQLAlchemy, pandas.read_sql,
     pymysql, or raw engine connections.

6. Extended P2 tool contracts:
   - Implement only the typed contracts required by metric/tool execution:
     get_metric_definition, get_schema_context, and
     rank_root_causes/contribution ranking.
   - METRIC_NOT_FOUND and SCHEMA_CONTEXT_MISSING must be typed errors.
   - Defer verify_evidence/search_memory/create_operation_task to Matrix P3
     continuation and list as Remaining deviations, not Known shortcuts.

REQUIRED FILES
Must create/update:
- metric_rca/services/metric_service.py
- metric_rca/services/anomaly_service.py
- metric_rca/services/attribution_service.py
- metric_rca/agent/tools/detect_anomaly.py
- metric_rca/agent/tools/drilldown_dimension.py
- metric_rca/agent/tools/fetch_related_signal.py
- metric_rca/agent/tools/calculate_contribution.py
- metric_rca/agent/tools/schemas.py if useful
- metric_rca/domain/models.py only if new typed contracts are needed
- metric_rca/guardrails/renderer.py for exact same-weekday baseline rendering
- tests/test_anomaly.py
- tests/test_attribution.py
- tests/test_tools.py
- tests/test_zero_fallback.py additions
- tests/test_renderer.py additions for baseline rendering

FORBIDDEN
- No direct DB read in services.
- No direct DB read in tools except through MetricRepository.execute_plan.
- No raw SQL string construction in tools except via SQLRenderer.
- No pandas.read_sql, create_engine, pymysql, or conn.execute in tools/services.
- No root cause candidate without current-run Evidence.
- No fabricated evidence payload.
- No empty-data attribution.
- No graph.py implementation in this phase.
- No service function writes final report.
- No use of anomaly_ground_truth outside seed/eval/tests.
- No broad except Exception: continue.
- No fallback provider/default config substitution.
- No hardcoded answers for the 5 eval cases.

TDD / PROOF-TEST-FIRST
Before application code, add or update tests that fail against shortcuts:
1. Baseline exactness test: previous 4 same weekdays only; broad 28-day BETWEEN must fail.
2. test_detect_anomaly_paid_ads_flagged.
3. test_detect_anomaly_no_anomaly_returns_no_anomaly_observation.
4. test_detect_anomaly_sample_n_lt_3_returns_insufficient_baseline_data.
5. test_detect_anomaly_threshold_boundaries.
6. test_attribution_paid_ads_contribution_top1_campaign_traffic_drop.
7. test_attribution_stockout_electronics_top1_stockout.
8. test_attribution_mobile_cvr_top1_conversion_drop.
9. test_attribution_refund_rate_uses_increase_direction.
10. test_gmv_decomposition_uses_uv_pay_cvr_aov_not_pay_orders.
11. test_empty_rows_do_not_create_candidate.
12. test_each_tool_uses_renderer_guard_repository_spy.
13. test_tool_persists_evidence_and_sql_audit.
14. test_tool_bad_dimension_returns_DIMENSION_NOT_ALLOWED.
15. test_tool_args_reject_extra_fields.
16. test_runtime_code_does_not_read_anomaly_ground_truth.
17. test_tool_guard_rejection_returns_typed_error_and_does_not_call_execute_plan.
18. test_persisted_evidence_id_is_run_scoped_unique.
19. test_services_do_not_import_db_or_repository_modules.
20. test_fetch_related_signal_covers_campaign_inventory_conversion_refund_quality.

COMMANDS
Run:
- make seed
- pytest -q tests/test_anomaly.py tests/test_attribution.py tests/test_tools.py tests/test_zero_fallback.py
- make test
- python -W error::ResourceWarning -m unittest discover -s tests -v

ACCEPTANCE CHECKS
- agent/tools/*.py are real files with behavior, not re-export shells.
- Each tool returns Observation and Evidence through a typed ToolResult.
- Every data tool writes evidence row and sql_audit row for current run.
- Repository spy proves execute_plan is called after guard_status passed.
- Runtime code grep finds no anomaly_ground_truth usage in metric_rca/services
  or metric_rca/agent.
- Attribution with empty rows fails typed; it never emits root cause.
- Services have no DB/repository imports.
- Existing P1 guard/schema/seed/repository tests remain green.
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
