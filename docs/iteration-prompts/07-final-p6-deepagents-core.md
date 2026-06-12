# Prompt 7 - Final P6: deepagents Core Migration

```text
You are working in the MetricRCA repository after MVP phases P1-P5 are
complete on branch codex/p4-p5-api-ui-eval-final. Work on a new branch
codex/p6-deepagents-core forked from that branch.

MANDATORY PRELUDE
Before applying this phase prompt, read and obey, in this order:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md (mandatory post-phase review)
3. docs/final-design/ (ALL five files) - this is the v2 design source of truth
4. docs/reference/decisions.md ADL-0007 and ADL-0006

SOURCE OF TRUTH PRIORITY FOR THIS PHASE
1. Current user instruction
2. AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md
3. docs/final-design/ (v2 design, overrides docs/MetricRCA.md where they
   conflict; you must sync MetricRCA.md as part of this phase)
4. docs/MetricRCA.md, docs/COMPLIANCE_MATRIX.md

TARGET
Implement Final P6 exactly as specified in docs/final-design/04-phase-plan.md
section "P6": migrate metric_rca/agent/ from the hand-written LangGraph
StateGraph to deepagents, with guard semantics moved to GuardMiddleware and
RunOrchestrator, per docs/final-design/01-architecture.md.

DOCS-FIRST REQUIREMENT (do this before writing any implementation code)
- Update docs/MetricRCA.md sections 5 and 6: mark the v1 StateGraph/ReAct
  design as "v1 (superseded by docs/final-design, kept as appendix)" and add
  v2 sections describing the deepagents architecture. Do not delete v1 text.
- Update docs/COMPLIANCE_MATRIX.md: rewrite the StateGraph/node/router rows
  into middleware/orchestrator rows. Every rewritten row must name a concrete
  proof test that would fail against a shortcut implementation.

IMPLEMENTATION REQUIREMENTS (see final-design 01/02/03 for full detail)
- Pin exact versions of deepagents and its langchain/langgraph dependencies
  in pyproject.toml. Verify every deepagents API you use against the pinned
  version's official docs, not memory. Record pinned versions by updating the
  "后续跟进" section of ADL-0007 in docs/reference/decisions.md.
- New agent/ layout: runner.py (RunOrchestrator), factory.py, middleware.py
  (GuardMiddleware via wrap_tool_call), tools/ (langchain @tool wrappers with
  Pydantic extra="forbid" In/Out models), prompts.py, reflection.py,
  subagents.py may be a stub raising if multi_agent_enabled (P9 scope).
  Delete graph.py, state.py, react.py, nodes/.
- LLM is REQUIRED: Settings.llm_model has no default; missing/unreachable
  LLM must produce typed LLM_REQUIRED_UNAVAILABLE, never a fallback policy.
  temperature=0.
- GuardMiddleware enforces, in order: tool whitelist, args schema validation
  (ACTION_SCHEMA_INVALID short-circuit), deterministic budget counters
  (max_steps/max_query/max_drilldown_depth, run-scoped, not LLM-visible;
  BUDGET_EXCEEDED short-circuit), execution, trace_step persistence with the
  new token_usage JSON column, and the invariant that every data-fetching
  tool call yields an evidence_id (missing -> typed error).
- Disable deepagents builtin filesystem tools; keep write_todos. Add a
  differential test proving the agent's exposed tool set equals exactly the
  registered whitelist plus the planning tool.
- Reflection runs in RunOrchestrator AFTER the agent loop, reading persisted
  artifacts only. One repair re-entry max (same thread via checkpointer),
  then REFLECTION_REPAIR_FAILED. Keep all eight v1 reflection checks.
- no_anomaly contract: orchestrator-side finalization check; violation ->
  NO_ANOMALY_CONTRACT_VIOLATED, run failed. Never a fabricated attribution.
- Preserve unchanged invariants: QuerySpec -> SQLRenderer -> SQLGuard ->
  Repository as the only metric-fact path; MetadataRepository -> MetricService
  as the only metadata path (no hardcoded metric/dimension/family lists
  anywhere in agent code); ADL-0006 report projection (numeric values only in
  numeric_claims bound to persisted Evidence).
- DDL: ALTER trace_step ADD token_usage JSON NULL (schema.sql + seed rebuild).
- Rewrite the test migration list from final-design/04-phase-plan.md:
  test_orchestrator.py, test_middleware.py, new test_zero_fallback.py with all
  eight v2 negative scenarios, adapted test_reflection.py and test_tools.py.
  All other existing tests (guard/renderer/seed/api/eval/memory/...) must stay
  green untouched.

ZERO FALLBACK NEGATIVE TESTS (all must exist and fail against shortcuts)
1. LLM unavailable -> LLM_REQUIRED_UNAVAILABLE, run failed, no run proceeds.
2. Illegal tool args -> ACTION_SCHEMA_INVALID observation, tool not executed.
3. Budget exhausted then LLM attempts another data tool -> run failed.
4. SQLGuard rejection cannot be bypassed (original SQL never executed).
5. no_anomaly run with drilldown/rank trace or task -> failed.
6. Reflection repair exhausted -> REFLECTION_REPAIR_FAILED, no report.
7. Memory read/write failure -> run failed (memory required).
8. Empty result set -> typed insufficient-evidence path, no attribute/rank.

ACCEPTANCE (verify and report actual command output, no claims without runs)
- make up && make seed && PATH=.venv/bin:$PATH make test all green.
- All 5 MVP eval cases green result-level via make eval (LLM configured).
- SQL safety 100% in eval summary.
- COMPLIANCE_MATRIX has no red rows; docs updated before code (commit order
  must show docs/matrix commit(s) preceding implementation commits).

FORBIDDEN SHORTCUTS
- Re-introducing a deterministic action-selection policy "for tests".
- Mocking the LLM inside production code paths (test fixtures may fake the
  model at the factory boundary only, and zero-fallback tests must prove the
  production path requires a real configured model).
- Keeping graph.py alive behind an import shim.
- Weakening any proof test to fit the migration.

Follow the global iteration rules for commit granularity and the mandatory
post-phase self-review from 06-review-checklist.md before declaring done.
```
