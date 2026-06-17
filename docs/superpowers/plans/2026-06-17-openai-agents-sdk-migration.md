# OpenAI Agents SDK Migration Implementation Plan

**Goal:** Replace the LangChain/DeepAgents runtime with an OpenAI Agents SDK boundary while moving RCA control flow into deterministic code and finishing Phase B eval requirements.

**Acceptance criteria:**
- No runtime dependency on `deepagents`, `langchain`, `langchain-core`, `langchain-openai`, or `langgraph`.
- Natural-language intent parsing still goes through an LLM structured-output agent, then deterministic validation.
- RCA flow is compiled into a typed `RcaPlan` and executed by deterministic gates/tools, not prompt-only sequencing.
- Query access remains `QuerySpec -> SQLRenderer -> SQLGuard -> Repository`.
- Original 20 eval cases stay green; 28/28 Phase B passes on two consecutive runs.
- No eval harness files are modified and no Python keyword/regex semantic intent mapper is introduced.

**Primary files/systems:**
- New runtime: `metric_rca/runtime/*`
- New intelligence boundary: `metric_rca/intelligence/*`
- Business policy extraction: `metric_rca/business/*`
- Existing deterministic core: `metric_rca/guardrails/*`, `metric_rca/repositories/*`, `metric_rca/services/attribution_service.py`, `metric_rca/services/adtributor_service.py`, `metric_rca/reporting/*`
- Existing agent API/eval entrypoints: `metric_rca/agent/runner.py`, `metric_rca/api/*`, `metric_rca/evals/*`
- Dependency metadata: `pyproject.toml`

**Validation:**
- Focused unit/integration tests for plan compilation, action gates, evidence graph, tool execution, intent agent adapter, Phase B failure regressions.
- `PATH=.venv/bin:$PATH make test`
- PTV loop with prediction files and `make eval-stream` / `make eval-gaps`
- Final `make eval` twice after 28/28 is reached.

## Task 1: Dependency And API Surface Cutover

**Addresses:** remove DeepAgents/LangChain runtime, keep OpenAI-compatible LLM requirement.

**Files:** `pyproject.toml`, `metric_rca/config/settings.py`, `metric_rca/services/llm_client.py`, dependency tests.

**Work:** Add `openai-agents` once package access is available. Remove DeepAgents/LangChain/LangGraph dependencies after replacement tests are in place. Replace LangChain exception types with local typed errors or OpenAI SDK exceptions at the boundary.

**Validation:** Import checks prove `metric_rca` loads without LangChain installed; dependency contract tests expect `openai-agents` and reject old agent runtime dependencies.

**Stop/ask if:** OpenAI Agents SDK package cannot be installed or official docs/API shape cannot be verified.

## Task 2: Typed Runtime Models

**Addresses:** deterministic plan graph and action ledger.

**Files:** `metric_rca/runtime/plan_models.py`, `metric_rca/runtime/run_context.py`, `metric_rca/runtime/evidence_graph.py`.

**Work:** Add `RcaAction`, `RcaPlan`, `ExecutionResult`, `GateDecision`, and `EvidenceGraph`. Keep models strict via existing `StrictModel`. Do not include natural-language question text in plan-compilation decisions except as already parsed `ParsedIntent`.

**Validation:** Unit tests cover valid/invalid action schemas, produced/requires evidence aliases, no-anomaly stop state, and evidence graph alias lookup.

**Stop/ask if:** Existing persisted evidence schema cannot represent required action/evidence graph facts without a migration.

## Task 3: Agents SDK Intent Boundary

**Addresses:** LLM-first intent, no Python semantic mapper.

**Files:** `metric_rca/intelligence/intent_agent.py`, `metric_rca/intelligence/intent_compiler.py`, `metric_rca/services/metric_service.py`, tests currently covering `LLMIntentPlanner`.

**Work:** Replace LangChain structured-output planner with Agents SDK `Agent(..., output_type=ParsedIntentOut)` and `Runner.run`. Preserve deterministic post-validation of metric, date, family, dimensions, filters, and supported values. Keep prompt guidance for aliases/date semantics inside the LLM instructions, not Python branches.

**Validation:** Existing metadata/intent tests pass without LangChain imports. New tests inject a fake SDK runner and verify typed parse failures.

**Stop/ask if:** SDK structured output API differs from the search-result/docs assumptions and cannot be verified offline.

## Task 4: Deterministic Plan Compiler

**Addresses:** move business flow from prompt into code.

**Files:** `metric_rca/runtime/plan_compiler.py`, `metric_rca/business/discovery_policy.py`, `metric_rca/business/signal_policy.py`, `metric_rca/business/factor_graph.py`.

**Work:** Compile `ParsedIntent`, metadata, and memory priors into `RcaPlan`. Required broad policies:
- GMV/net GMV/AOV/UV: compile required drilldowns and factor-graph checks.
- Rate metrics: compile metric-specific discovery from structured metric metadata/policy.
- Explicit slices: compile target dimension/value path only.
- No-anomaly branch remains runtime conditional after `detect_anomaly`.

**Validation:** Compiler tests assert action kinds/args for explicit slices, broad GMV, broad UV, pay CVR, refund rate, no filters, and memory-prior ordering. Tests must not assert source prompt text.

**Stop/ask if:** Metric metadata is insufficient to derive allowed discovery dimensions without hardcoding a policy table.

## Task 5: Action Gate Extraction

**Addresses:** replace LangChain-bound `GuardMiddleware`.

**Files:** `metric_rca/runtime/action_gate.py`, `metric_rca/runtime/trace_bridge.py`, tests currently in `tests/test_middleware.py` and `tests/test_zero_fallback.py`.

**Work:** Split existing guard behavior into schema, scope, explicit-filter, evidence-chain, discovery-policy, no-anomaly, repair, and budget validators. Return typed `GateDecision` instead of `ToolMessage`.

**Validation:** Port middleware tests to gate tests. Every denial has a typed error code. No broad exception swallowing.

**Stop/ask if:** Any current middleware behavior depends on LangChain-only message state that is not present in repository trace/evidence.

## Task 6: Tool Executor And SDK Function Tool Registry

**Addresses:** remove `StructuredTool` wrappers and graph introspection.

**Files:** `metric_rca/runtime/sdk_tools.py`, `metric_rca/runtime/plan_executor.py`, existing tool modules under `metric_rca/agent/tools/*`.

**Work:** Wrap deterministic tools behind a local registry with stable names and schemas. SDK function tools may call the same executor, but RCA execution should be driven by `RcaPlanExecutor`, not by free-form model tool selection. Delete `_compiled_tool_names()` and graph internals checks.

**Validation:** Registry tests prove only whitelisted RCA tools are exposed. Plan executor tests prove `run_id`, repository, metric service, and renderer are injected outside model control.

**Stop/ask if:** SDK tool schema generation requires package features that cannot be imported locally.

## Task 7: Phase B Business Fixes In The New Runtime

**Addresses:** remaining B6 failures and Phase B hard gates.

**Files:** `metric_rca/services/anomaly_service.py`, `metric_rca/services/attribution_service.py`, `metric_rca/business/discovery_policy.py`, `metric_rca/business/signal_policy.py`, tests added in the WIP snapshot.

**Work:** Carry forward the three known red tests:
- Positive magnitude spikes are anomalies even when not the metric's bad direction.
- Attribution severity uses element-level relative movement, so close-contribution direct KPI cases rank the sharper movement correctly.
- Broad discovery policy defaults by structured metric_id when exact question_family is not specific enough.

**Validation:** Focused tests for C23/C24/C25 regressions pass before full test/eval.

**Stop/ask if:** Fixing C23 or C25 requires case-id-specific behavior or eval harness edits.

## Task 8: RunService Cutover

**Addresses:** new end-to-end runtime path.

**Files:** `metric_rca/runtime/run_service.py`, `metric_rca/agent/runner.py`, API wiring, eval runner wiring.

**Work:** Introduce `RunService.run(question, run_id)` as the main orchestration path. Keep existing API/eval call signatures stable where possible, but route through the new runtime. Preserve memory read/write semantics as planning influence only.

**Validation:** Existing orchestrator/eval tests pass after being ported to `RunService`. Memory pollution tests remain green.

**Stop/ask if:** Public API contract changes are needed before the job-style API redesign.

## Task 9: Reports, Tasks, Memory, And Trace Ledger

**Addresses:** evidence traceability and future UI/action-ledger needs.

**Files:** `metric_rca/runtime/trace_bridge.py`, `metric_rca/reporting/*`, `metric_rca/memory/*`, repository trace/action APIs if needed.

**Work:** Keep current persisted evidence as the source of report truth. Add action-ledger-compatible records through existing trace tables first; defer DB schema expansion unless required for eval/traceability.

**Validation:** `report_traceable_rate=1.0`, memory pollution checks, and trace step tests pass.

**Stop/ask if:** Adding `rca_action_ledger` requires a schema migration outside current project conventions.

## Task 10: Eval Loop And Final Review

**Addresses:** Phase B acceptance.

**Files:** eval artifacts under `eval_out/`, `docs/reference/decisions.md`, review checklist outputs.

**Work:** Resume PTV with new predictions after each major runtime cutover. Record ADLs for nontrivial architecture changes. Run checklist scans A/E and final validation.

**Validation:** `make test`, then 28/28 eval green twice; checklist A/E clean; no forbidden eval file changes.

**Stop/ask if:** OpenAI API/network remains unavailable for live eval after local tests pass.
