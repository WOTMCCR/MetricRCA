# MetricRCA Implementation Contract

This repository is intentionally at the docs-only implementation-plan stage.
Future implementation work must satisfy the engineering design and executable
checklist, not merely make a small eval set pass.

## 1. Source Of Truth

- `docs/MetricRCA.md` is the engineering design source of truth.
- `docs/MetricRCA-roadmap-checklist.md` is the executable Definition of Done.
- `docs/deep-research-report.md` is background/interview material only.
- `AGENTS.md` and this contract define repository-level operating rules.

If these documents appear to conflict, use this priority:

1. Current user instruction in the conversation.
2. `AGENTS.md` and this implementation contract.
3. `docs/MetricRCA.md`.
4. `docs/MetricRCA-roadmap-checklist.md`.
5. `docs/deep-research-report.md`.

Do not use the research report to expand MVP scope with MCP, Multi-Agent,
Vector DB, arbitrary Text-to-SQL, auth, multi-tenant behavior, or dashboard-heavy
UI unless the user explicitly requests that scope.

## 2. MVP Means Docs Compliance

Passing the 5 MVP eval cases is necessary but not sufficient.

The implementation task is docs compliance, not "minimal code that makes 5 cases
pass." Do not redefine MVP as a small deterministic prototype. In this
repository, MVP means the architecture, data contracts, behavior, error paths,
tests, and user-facing surfaces listed in `docs/MetricRCA.md` and
`docs/MetricRCA-roadmap-checklist.md`.

A future implementation is acceptable only when:

- It satisfies the relevant design and checklist sections.
- Tests would fail against a shortcut implementation.
- Required architecture is not replaced by simpler placeholders.
- Required modules contain real responsibilities, not empty files or re-export
  shells.
- Any scope deviation is explicit, documented, and approved by the user.

## 3. Forbidden Shortcuts

These shortcuts are P0 violations:

- No plain sequential function pretending to be LangGraph.
- No empty placeholder node/tool modules.
- No CLI print pretending to be FastAPI.
- No print(json) pretending to be a frontend debug UI.
- No runtime hardcoded metric metadata or schema context pretending to satisfy
  DB-backed metadata contracts.
- No regex SQLGuard pretending to be sqlglot AST guard.
- No hardcoded eval success.
- No `dangerous_sql_blocked = null`.
- No broad `except Exception: continue`.
- No empty-data attribution.
- No SQLGuard bypass.
- No report generation after failed Reflection.
- No Memory-derived conclusion without current-run Evidence.
- No LLM-only bypass.
- No default provider/config substitution.
- No mock/stub/demo production paths.
- No silent degradation after required dependency failure.
- No root cause candidate without current-run passed Evidence.
- No no-anomaly case that creates an operation task.

Passing tests by bypassing core logic is failure, not success.

## 4. Required Architecture

Future application code must implement the documented architecture directly.

### Agent Graph

- `metric_rca/agent/graph.py` must build a real LangGraph `StateGraph(RCAState)`.
- The graph must use `START`, `END`, `add_node`, `add_edge`, and
  `add_conditional_edges`.
- `run_rca()` may be a wrapper, but it must invoke the compiled graph.
- Business termination must use settings such as `max_steps`, `max_query`,
  `max_drilldown_depth`, and `max_repair`, not LangGraph `recursion_limit`.

### Required Agent Nodes

Each required node must be a real module with real behavior:

- `metric_rca/agent/nodes/parse_question.py`
- `metric_rca/agent/nodes/read_memory.py`
- `metric_rca/agent/nodes/plan_init.py`
- `metric_rca/agent/nodes/react_step.py`
- `metric_rca/agent/nodes/execute_tool.py`
- `metric_rca/agent/nodes/attribute_rank.py`
- `metric_rca/agent/nodes/reflection_verify.py`
- `metric_rca/agent/nodes/generate_report.py`
- `metric_rca/agent/nodes/create_tasks.py`
- `metric_rca/agent/nodes/write_memory.py`
- `metric_rca/agent/nodes/error_return.py`

### ReAct Loop

- ReAct must be a real `AgentAction -> Observation -> Evidence` loop.
- `react_step` produces an `AgentAction` from current `RCAState`.
- `execute_tool` executes only whitelisted `AgentAction` values.
- Each step appends `AgentAction` to `state.actions`.
- Each tool result appends `Observation` to `state.observations`.
- Each tool-created `Evidence` appends to `state.evidences`.
- Every step writes `trace_step`.
- Invalid action must create
  `Observation(ok=False, error_code="ACTION_SCHEMA_INVALID")` and must not
  execute any tool.
- If LLM is required and unavailable, return `LLM_REQUIRED_UNAVAILABLE`.

### Tool And Data Access Layer

The deterministic tool layer must be real and file-backed:

- `metric_rca/agent/tools/detect_anomaly.py`
- `metric_rca/agent/tools/drilldown_dimension.py`
- `metric_rca/agent/tools/fetch_related_signal.py`
- `metric_rca/agent/tools/calculate_contribution.py`

Each tool must:

- Accept typed args.
- Create `QuerySpec`.
- Go through `SQLRenderer -> SQLGuard -> Repository`.
- Return `Observation + Evidence`.
- Never bypass `SQLGuard`.
- Write `sql_audit` through the repository.

`QuerySpec -> SQLRenderer -> SQLGuard -> Repository` is the only data access
path for metric facts and related signals.

Metric metadata is not metric-fact data, but it still must be a real persisted
metadata contract. `get_metric_definition` and `get_schema_context` must be
backed by `metric_definition`, schema metadata, or an explicit metadata
repository. Runtime services must not duplicate metric definitions, schema
context, seeded dimension values, channel/category lists, or product IDs as
hardcoded constants. Fixed MVP question families constrain parsing; they do not
authorize hardcoded metadata.

### SQLGuard

- SQLGuard must use sqlglot AST.
- Regex/string checks may be supplementary only; they cannot be the final guard.
- It must reject multiple statements, non-SELECT statements, DML/DDL, command-like
  operations, `SELECT *`, CTEs, subqueries, derived tables, non-whitelisted
  tables/columns, missing fact table, missing `business_date` filter on fact
  tables, missing `LIMIT`, and non-renderer-generated joins.
- It must allow only renderer-generated whitelist `INNER JOIN` patterns.
- It must preserve `sql_hash` and support `sql_audit` writes.

### Reflection

Reflection must be a rule verifier, not a stub or second summary pass. It must
check evidence coverage, current-run evidence, `guard_status`, numeric
traceability, time range, metric consistency, attribution coverage, no-anomaly
task behavior, causal language, and repair limits.

If an error issue has a suggested action, the graph must route back through the
legal ReAct/tool/query path, then verify again. If repair still fails, return
`REFLECTION_REPAIR_FAILED`. Do not generate a report after failed Reflection.

### Memory

Memory must be constrained and auditable:

- Implement `memory_repo.py`, not a re-export-only repository alias.
- `read_memory` reads case/session memory.
- `write_memory` writes case/session memory.
- Use exact keys such as `gmv|channel`.
- Memory hits may only influence drilldown priority.
- Memory must never become the final conclusion.
- Store and respect `confidence`, `source`, `version`, and `ttl_days`.
- Low-confidence memory must not influence planning.
- Required memory failures must return typed errors.

### API, UI, And Eval

- FastAPI must be real and expose the documented endpoints:
  - `POST /api/rca/runs`
  - `GET /api/rca/runs/{run_id}`
  - `GET /api/rca/runs/{run_id}/trace`
  - `GET /api/rca/runs/{run_id}/evidence`
  - `POST /api/evals/run`
  - `GET /api/evals/{eval_id}`
  - `GET /health`
- React/Vite must be a real debug UI that reads FastAPI persisted artifacts, not `print(json)`.
- Eval must read and validate against `anomaly_ground_truth`.
- Eval must write `eval_run` and `eval_case_result`.
- `dangerous_sql_blocked` must be a real boolean.
- `no_anomaly_correct` must check that no `operation_task` was created.

## 5. Required Work Process

Before implementation:

1. Read `AGENTS.md`, this contract, `docs/MetricRCA.md`,
   `docs/MetricRCA-roadmap-checklist.md`, `docs/deep-research-report.md`, and
   `docs/env-setup.md`.
2. Inspect the current source and tests.
3. The docs compliance matrix is persisted at `docs/COMPLIANCE_MATRIX.md` with
   columns: Requirement, Docs reference / section, Required files, Required
   behavior, Tests that must prove it, Phase, Shortcut risk to avoid. It is the
   binding implementation gate (27 rows; current status of every row is
   `missing` at base commit). It was Codex-reviewed; keep it current.
4. Do not start implementation until your task is mapped to its matrix rows and
   proof tests. Implement by the matrix Phase order unless given a narrower task.

During implementation:

- Implement by documented phases unless the user gives a narrower task.
- For each phase, write failing tests first or update tests so they encode docs
  requirements.
- Do not weaken tests to fit shortcuts.
- Do not optimize for green tests by simplifying architecture.
- Do not add out-of-scope features unless explicitly requested.
- Keep failure behavior fail-fast with typed errors.
- Actively search for fallback-like behavior when reviewing or modifying code.
- Actively search for metadata-hardcoding shortcuts when touching parser,
  service, tool, repository, schema, or seed code. If any remain, list them as
  remaining deviations and do not report `Known shortcuts: []`.

Before claiming completion:

- Run the relevant verification commands for the changed phase.
- For full implementation completion, run:
  - `make seed`
  - `make eval`
  - `make test`
  - `python -W error::ResourceWarning -m unittest discover -s tests -v`
- The final response must include commands run, test output summary, fallback-like
  code touched, and remaining deviations.

If any requirement remains unsatisfied, list it as a remaining deviation. Do not
claim docs compliance unless tests prove it.
