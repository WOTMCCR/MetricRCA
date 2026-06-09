# Prompt 2 - Matrix P3 Part A: Real LangGraph + ReAct + Trace

```text
You are working in the MetricRCA repository after Matrix P2 is complete.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md (especially Rules 5, 17)
docs/iteration-prompts/06-review-checklist.md (mandatory post-phase review)
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
  5. docs/COMPLIANCE_MATRIX.md
- Preserve QuerySpec -> SQLRenderer -> SQLGuard ->
  MetricRepository.execute_plan as the only metric-fact data path.
- Preserve MetadataRepository -> MetricService as the only metadata path.
  Graph nodes must NOT import or construct MetricDefinition directly, nor
  hardcode metric/dimension/family lists. All metadata access flows through
  MetricService (which wraps MetadataRepository).
- parse_question node calls MetricService.parse_question(), which delegates
  to LLMIntentPlanner. Do not re-implement intent parsing in graph code.
- QuestionFamily is the single Literal type alias in metric_contracts.py.
  If a new question family is needed, update QuestionFamily there; both
  ParsedIntent, LLMIntentPlanner output schema, and SUPPORTED_QUESTION_FAMILIES
  derive from it automatically via get_args().
- This phase must implement a real LangGraph StateGraph and ReAct loop.
- This phase must not claim full Matrix P3 completion until Reflection+Memory
  hardening in the next phase is done.
- Passing 5 cases is not sufficient; architecture proof tests matter.

TARGET
Implement Matrix P3 Part A:
- Real LangGraph StateGraph(RCAState)
- Real ReAct AgentAction -> Observation -> Evidence loop
- Real node modules
- Trace writing
- Core fail-closed reflection gate
- Zero-fallback negative tests that are possible before full memory hardening

MATRIX ROWS MAPPING
- Row 11: AgentAction & ReAct policy
- Row 13: LangGraph StateGraph
- Row 14: Agent node files
- Row 15: TraceStep / Observability
- Row 16: Evidence binding continuation
- Row 19 partial: Core deterministic reflection gate; full repair checks
  completed next phase
- Row 20 partial: read_memory/write_memory nodes may run with
  memory_enabled=false for graph E2E; full MemoryRepository completed next
  phase
- Row 24 partial: zero-fallback negatives for action/LLM/SQL/no_anomaly/empty
  result
- Keep P1 and P2 tests green.

SOURCE OF TRUTH
Read before modifying code:
- AGENTS.md
- docs/IMPLEMENTATION_CONTRACT.md sections Required Architecture, ReAct Loop,
  Reflection, Memory
- docs/COMPLIANCE_MATRIX.md rows 11, 13, 14, 15, 16, 19, 20, 24
- docs/MetricRCA.md sections 5, 6, 7, 8, 15, 16, 18
- docs/MetricRCA-roadmap-checklist.md sections 2.2, 3.1, 6, 7, 9 phase 3, 12

DEPENDENCIES
- langchain-openai is already declared from P2 fix-001. Add langgraph with
  bounded compatible constraints. Do not re-declare langchain-openai.
- Update tests/test_project_contract.py so Phase 3 dependencies (langgraph)
  are no longer forbidden.
- Do not add FastAPI, uvicorn, streamlit in this phase. httpx[socks] is
  already declared from P2 fix-001.

P2 CONTEXT (what is already implemented — do not duplicate)
- metric_rca/repositories/metadata_repository.py — DB-backed metadata
- metric_rca/services/metric_service.py — MetricService with lazy
  LLMIntentPlanner; metadata-only methods work without LLM API key
- metric_rca/services/intent_planner.py — LLMIntentPlanner via LangChain
  OpenAI structured output
- metric_rca/services/metric_contracts.py — QuestionFamily type alias,
  SUPPORTED_QUESTION_FAMILIES, ParsedIntent, MetricServiceError
- metric_rca/agent/tools/*.py — 4 deterministic tools accepting
  metric_service via DI
- metric_rca/services/anomaly_service.py — pure computation
- metric_rca/services/attribution_service.py — pure computation,
  root_cause_type from Settings config
Graph nodes should import and call these, not rewrite them.

SCOPE
1. Implement RCAState:
   - File: metric_rca/agent/state.py
   - TypedDict total=False.
   - List fields must use Annotated[list, operator.add] reducers:
     actions, observations, evidences
   - Required fields:
     run_id, question, metric_id, target_date, parsed_spec, memory_hits,
     actions, observations, evidences, anomaly, candidates, reflection, report,
     step_count, query_count, drilldown_depth, repair_count, error_code, status

2. Implement ReAct policy:
   - File: metric_rca/agent/react.py
   - ALLOWED_ACTIONS exactly:
     detect_anomaly
     drilldown_dimension
     fetch_related_signal
     calculate_contribution
     finish
   - Implement validate_action(action: AgentAction) -> AgentAction or typed
     error.
   - Invalid action or bad args:
     Observation(ok=False, error_code="ACTION_SCHEMA_INVALID")
     Must not execute any tool.
   - Deterministic-primary policy:
     a. first action: detect_anomaly
     b. if E1 reports NO_ANOMALY_DETECTED: finish with status no_anomaly
     c. after anomaly: choose drilldown dimension based on parsed intent and
        memory priority
     d. after drilldown: fetch related signal
     e. after related signal: calculate_contribution
     f. after contribution and enough evidence: finish
   - If settings.llm_required=True and no provider/config is available:
     LLM_REQUIRED_UNAVAILABLE and error_return.
   - LLM, if present later, may only select whitelisted actions; it must not
     write SQL or facts.

3. Implement real node files:
   - metric_rca/agent/nodes/parse_question.py
   - metric_rca/agent/nodes/read_memory.py
   - metric_rca/agent/nodes/plan_init.py
   - metric_rca/agent/nodes/react_step.py
   - metric_rca/agent/nodes/execute_tool.py
   - metric_rca/agent/nodes/attribute_rank.py
   - metric_rca/agent/nodes/reflection_verify.py
   - metric_rca/agent/nodes/generate_report.py
   - metric_rca/agent/nodes/create_tasks.py
   - metric_rca/agent/nodes/write_memory.py
   - metric_rca/agent/nodes/error_return.py
   Each file must contain real behavior. No empty file, no re-export shell, no
   hiding node logic inside graph.py.
   CRITICAL: Nodes receive MetricService, MetricRepository, and SQLRenderer
   via graph state or dependency injection. Nodes must NOT:
   - import MetricDefinition and construct instances
   - hardcode metric/dimension/family lists
   - duplicate intent parsing logic (call MetricService.parse_question)
   - bypass SQLRenderer/SQLGuard for fact queries
   - use broad except Exception

4. Implement graph:
   - File: metric_rca/agent/graph.py
   - Must build a real LangGraph StateGraph(RCAState).
   - Must use START, END, add_node, add_edge, add_conditional_edges.
   - run_rca() may be a wrapper, but it must invoke the compiled graph.
   - Graph construction must create MetadataRepository(engine) and
     MetricService(metadata_repo, settings) and pass them to nodes.
     This is the only place where MetricService is constructed.
   - Business limits must use settings:
     max_steps=8
     max_query=12
     max_drilldown_depth=2
     max_repair=1
   - Do not rely on LangGraph recursion_limit as business control.

5. Required control flow:
   START -> parse_question -> read_memory -> plan_init -> react_step
   react_step -> execute_tool for tool actions
   execute_tool -> react_step
   react_step -> attribute_rank on finish/evidence enough
   react_step -> generate_report(status=no_anomaly) on NO_ANOMALY_DETECTED
   attribute_rank -> reflection_verify
   reflection_verify -> generate_report if passed
   reflection_verify -> react_step if repairable and repair_left
   reflection_verify -> error_return if failed
   generate_report -> create_tasks if confirmed/likely candidate
   generate_report no_anomaly -> write_memory
   create_tasks -> write_memory
   error_return -> write_memory
   write_memory -> END

6. Trace and persistence:
   - Implement metric_rca/observability/trace.py if useful.
   - Every node writes trace_step.
   - execute_tool also records action and tool result.
   - Every failure trace includes error_code.
   - seq must be contiguous per run_id.
   - latency_ms >= 0.
   - agent_run status and error_code must be persisted.

7. Evidence binding:
   - E1 detect_anomaly
   - E2 drilldown_dimension
   - E3 fetch_related_signal
   - E4 calculate_contribution
   - Confirmed/likely candidate must bind current-run E1-E4.
   - no_anomaly binds E1, skips attribute_rank and create_tasks.
   - Candidate without current-run evidence must be rejected by reflection.

8. Core reflection gate in this phase:
   - Implement metric_rca/agent/reflection.py with at least:
     a. every confirmed/likely candidate has evidence_ids
     b. evidence exists in current state
     c. evidence guard_status == passed
     d. current-run evidence only
     e. no_anomaly has no operation_task and no attribute_rank trace
     f. failed reflection must route to error_return and must not generate
        report
   - Full numeric traceability, repair suggested_action, and Memory hardening are
     completed in next phase. List them as Remaining deviations.

9. Memory nodes in this phase:
   - read_memory.py and write_memory.py must be real modules.
   - For graph E2E tests before memory_repo exists, configure
     memory_enabled=false explicitly.
   - If memory_enabled=true and memory_repo is unavailable, return typed
     MEMORY_READ_FAILED or MEMORY_WRITE_FAILED; do not silently no-op.
   - Full MemoryRepository behavior is next phase and must be listed as
     Remaining deviation.

REQUIRED FILES
Must create/update:
- metric_rca/agent/state.py
- metric_rca/agent/react.py
- metric_rca/agent/graph.py
- metric_rca/agent/reflection.py
- metric_rca/agent/nodes/parse_question.py
- metric_rca/agent/nodes/read_memory.py
- metric_rca/agent/nodes/plan_init.py
- metric_rca/agent/nodes/react_step.py
- metric_rca/agent/nodes/execute_tool.py
- metric_rca/agent/nodes/attribute_rank.py
- metric_rca/agent/nodes/reflection_verify.py
- metric_rca/agent/nodes/generate_report.py
- metric_rca/agent/nodes/create_tasks.py
- metric_rca/agent/nodes/write_memory.py
- metric_rca/agent/nodes/error_return.py
- metric_rca/observability/trace.py if needed
- pyproject.toml
- tests/test_graph.py
- tests/test_react.py
- tests/test_trace.py
- tests/test_zero_fallback.py additions
- tests/test_project_contract.py updates for Phase 3 deps

FORBIDDEN
- No sequential run_rca pretending to be graph.
- No graph.py giant function that directly calls every business function.
- No empty/re-export node modules.
- No invalid action executing tool.
- No report after failed reflection.
- No attribute_rank on no_anomaly.
- No create_tasks on no_anomaly.
- No GT leakage from anomaly_ground_truth.
- No root cause without current-run evidence.
- No Memory-derived conclusion.
- No broad except Exception: continue.
- No API/UI/eval implementation in this phase.
- No hardcoded metric/dimension/family lists in node modules. Nodes must use
  MetricService for all metadata access. (P2 fix-001 already purged this
  pattern — do not reintroduce it.)
- No constructing MetricDefinition objects in graph or node code.
- No reimplementing intent parsing logic in graph code. parse_question node
  must call MetricService.parse_question() which delegates to LLMIntentPlanner.

TDD / PROOF-TEST-FIRST
Add tests before implementation:
1. test_graph_contains_real_stategraph_start_end_nodes_and_conditional_edges
2. test_run_rca_invokes_compiled_graph_not_sequential_orchestrator
3. test_required_node_files_are_not_reexport_shells
4. test_reducers_accumulate_actions_observations_evidences
5. test_illegal_action_records_ACTION_SCHEMA_INVALID_and_does_not_execute_tool
6. test_llm_required_unavailable_fails
7. test_gmv_paid_ads_drop_e2e_through_graph_with_E1_to_E4
8. test_no_anomaly_generates_no_anomaly_report_skips_attribute_rank_and_create_tasks
9. test_failed_reflection_routes_error_return_and_no_report
10. test_trace_step_contiguous_seq_for_every_visited_node
11. test_each_evidence_step_has_sql_audit_row
12. test_tiny_max_steps_stops_by_business_limit_not_GraphRecursionError
13. test_runtime_graph_code_does_not_read_anomaly_ground_truth
14. test_empty_result_does_not_enter_attribute_rank
15. test_graph_and_node_modules_have_no_hardcoded_metric_metadata
    Source scan: metric_rca/agent/graph.py and metric_rca/agent/nodes/*.py
    must not contain MetricDefinition( construction, METRIC_DEFINITIONS,
    SCHEMA_CONTEXT, _CHANNELS, _CATEGORIES, or literal dimension values.

COMMANDS
Run:
- make seed
- pytest -q tests/test_react.py tests/test_graph.py tests/test_trace.py tests/test_zero_fallback.py
- make test
- python -W error::ResourceWarning -m unittest discover -s tests -v

ACCEPTANCE CHECKS
- metric_rca/agent/graph.py contains StateGraph, START, END, add_node, add_edge,
  add_conditional_edges.
- run_rca invokes compiled graph.
- Node files are real modules with behavior and source paths in their own files.
- Deterministic ReAct loop appends AgentAction, Observation, Evidence.
- Invalid actions do not execute tools.
- no_anomaly has E1 only, no attribute_rank trace, no operation_task.
- gmv_paid_ads_drop reaches campaign_traffic_drop only through current-run
  evidence.
- Failed reflection produces no report.
- Full Reflection repair and MemoryRepository are listed as Remaining
  deviations, not as completed work.
- All items in docs/iteration-prompts/06-review-checklist.md pass. Paste
  actual grep/scan output for sections A and E. A failing item is blocking.
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
