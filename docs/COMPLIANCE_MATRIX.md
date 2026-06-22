# MetricRCA — Current Compliance Matrix

**Current status:** MetricRCA uses OpenAI Agents SDK for structured intent
parsing and deterministic runtime modules for RCA planning/execution. The
current implementation gate is the user-requested hard cut away from legacy
agent stacks.

**Authority order:** current user instruction -> `AGENTS.md` ->
`docs/IMPLEMENTATION_CONTRACT.md` -> this matrix -> design/reference docs.

## Binding Rows

| # | Requirement | Required files | Required behavior | Proof tests |
|---|---|---|---|---|
| 1 | Dependencies | `pyproject.toml` | Runtime deps include `openai-agents`; runtime deps exclude legacy agent stacks. | `test_pyproject_declares_current_phase_dependencies` |
| 2 | Intent boundary | `services/intent_planner.py`, `services/metric_service.py` | Natural language to `ParsedIntent` goes through OpenAI Agents SDK structured output, then deterministic metadata validation. Python runtime code must not implement semantic keyword/regex alias mappers. | `tests/test_metadata_service.py` |
| 3 | Plan compiler | `runtime/plan_compiler.py`, `business/discovery_policy.py`, `business/signal_policy.py`, `business/attribution_experience.py` | `ParsedIntent` compiles to typed `RcaPlan`; explicit slices keep `E1/E2/E3/E4/E_rank`, while broad discovery inserts first-class `select_signal_element`. Experience and memory may reorder the complete policy-approved lane set, but cannot remove lanes or change canonical E4 source ordering. | `tests/test_runtime_plan.py`, `tests/test_attribution_experience.py`, `tests/test_multi_agent.py`, `tests/test_business_signal_policy.py` |
| 4 | Action gate | `runtime/action_gate.py`, `runtime/evidence_graph.py` | Actions are denied with typed error codes for metric/date scope violations, missing evidence, no-anomaly downstream calls, explicit-scope drift, and budget exhaustion. `select_signal_element` is a downstream data action. | `tests/test_runtime_action_gate.py`, `tests/test_middleware.py`, `tests/test_zero_fallback.py` |
| 5 | Tool executor | `runtime/sdk_tools.py`, deterministic tool modules | Tool registry exposes only MetricRCA tools including `select_signal_element`; runtime injects `run_id` and evidence ids; dynamic elements resolve only from current-run `E_select_*`; hidden per-candidate selection SQL is forbidden. | `tests/test_runtime_tool_executor.py`, `tests/test_tools.py` |
| 6 | Run service | `runtime/run_service.py`, `runtime/memory_service.py`, `agent/runner.py` compatibility entrypoint | `RunService` owns parse -> memory read -> compile -> execute -> reflect -> project -> memory write -> finish. `run_rca` routes through this runtime path. | `tests/test_runtime_run_service.py`, API/eval tests |
| 7 | Data path | `guardrails/*`, `repositories/*`, `agent/tools/*` | Metric facts are accessed only through `QuerySpec -> SQLRenderer -> SQLGuard -> Repository`. No raw SQL/model SQL execution bypass. | renderer/guard/repository/tool tests |
| 8 | Memory boundary | `memory/*`, `runtime/memory_service.py`, `runtime/plan_models.py`, `business/attribution_experience.py` | Memory is represented as `CasePrior` planning influence only. It may affect execution priority over existing policy lanes, but cannot change lane coverage, canonical E4 source ordering, evidence, contribution values, or the final conclusion. | `tests/test_memory.py`, `tests/test_attribution_experience.py`, `tests/test_runtime_plan.py`, `tests/test_runtime_run_service.py` |
| 9 | Eval integrity | `evals/*`, fixed eval data | Eval harness and ground truth remain fixed; scoring uses persisted artifacts and SQL safety checks. | `tests/test_eval.py`, `tests/test_eval_http_client.py`, `tests/test_eval_prediction.py` |
| 10 | Zero fallback | runtime/services/tools | Missing credentials, schema errors, invalid budgets, missing evidence, SQL failures, and reflection/report failures produce typed errors. No silent degradation or broad exception swallowing. | `tests/test_zero_fallback.py`, full `make test` |

## Build Sequence

1. `uv venv .venv && uv pip install -e .`
2. `PATH=.venv/bin:$PATH make test`
3. Predict-then-verify eval loop:
   `python -m metric_rca.evals.prediction ...`,
   `make eval-stream EVAL_ID=...`,
   `make eval-gaps EVAL_ID=...`
4. Final gate: `make test`, then two consecutive `make eval` passes.

## Hard Gates

- No eval-standard file edits.
- No runtime dependency or import of legacy agent stacks.
- No Python keyword/regex natural-language semantic intent mapper.
- No prompt/rule path may select signal type, root cause, factor graph, final
  candidate, or evidence chain outside policy/runtime evidence.
- No raw SQL/dataframe/direct engine data path in runtime RCA tools.
- No hidden dynamic selection outside `select_signal_element` / `E_select`.
- No silent fallback; every failure path has a typed `error_code`.
