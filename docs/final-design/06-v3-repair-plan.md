# MetricRCA v3 Repair Plan

> Status: accepted with minor contract amendments. This document supersedes the
> v2 deepagents final-design files for active production runtime decisions.

## 1. Runtime Boundary

- OpenAI Agents SDK is used only for structured intent parsing.
- The RCA loop is deterministic: `ParsedIntent -> RcaPlanCompiler ->
  RcaPlanExecutor -> ActionGate -> ToolExecutor -> QuerySpec -> SQLRenderer ->
  SQLGuard -> Repository -> Evidence -> Reflection -> Report`.
- The model never selects SQL, evidence ids, `signal_type`, factor graph,
  final candidate, or persisted conclusion.

## 2. Iteration Order

1. Iteration 0: v3 docs, compliance matrix, and legacy isolation.
2. Iteration 1: runtime memory integration, first-class `select_signal_element`,
   `E_select` evidence, and SQL audit-delta budget.
3. Iteration 2: canonical E4 `ContributionSet` and multi-cause scoring.
4. Iteration 3: policy registry and metadata-backed alias resolver.
5. Iteration 4: scenario registry, public/private cases, and seed profiles.
6. Iteration 5: eval acceptance suites with per-family and memory-treatment gates.

Iteration 0 is documentation and contract alignment only; it is not counted as
capability delivery. Iteration 1 is the first required vertical slice.

## 3. Iteration 1 Contracts

- Add `RuntimeMemoryService.read_priors`, `write_verified_case`, and
  `write_reflection_failure`.
- `memory_enabled=false` performs no memory IO.
- `memory_enabled=true` records a `memory_read` trace.
- `memory_required=true` turns read/write failures into typed terminal failures.
- Memory priors may contain planning hints such as preferred dimensions,
  preferred signal types, and weak root-cause priors. They must not contain
  direct answer-bearing fields such as `expected_element` or
  `expected_root_cause_type`.
- Dynamic discovery uses:
  `E1 -> E2_* -> E_select_* -> E3 -> E4 -> E_rank`.
- Explicit slices use:
  `E1 -> E2_* -> E3 -> E4 -> E_rank`; `E_select` is not mandatory.
- `select_signal_element` must use grouped current/baseline queries. Candidate
  count increases must not increase SQL count.
- `ToolExecutionResult.sql_count` is the declared count; repository
  `sql_audit` delta is authoritative. A mismatch fails
  `TOOL_SQL_COUNT_MISMATCH`.

## 4. Remaining v3 Gates

- E4 `contribution_set` becomes the canonical attribution source; legacy
  `selected_candidate/candidates` are derived projections only.
- Production `RunService` requires v3 `contribution_set`; legacy E4 projection is
  allowed only for historical API reads and isolated migration tests.
- Policy registry owns `metric_id x dimension -> signal_type`, discovery policy,
  and factor graph policy. Intent aliases may parse user wording, but no prompt
  or keyword rule may select a signal, root cause, factor graph, candidate, or
  evidence chain outside the registry.
- Regression cases split into public questions/tags and private ground truth.
  Public files must not contain answer-bearing fields, but natural business
  metric words such as GMV, sales, traffic, conversion rate, refund rate,
  stockout rate, and complaint rate are allowed in question text.
- `make seed` defaults to `regression`; `acceptance` and `stress` are opt-in.
  Destructive seed reset requires explicit allow or a local test DSN allowlist.
- Eval acceptance includes regression, blind, seed-sweep, mutation,
  memory-treatment, and acceptance suites. Aggregate top-k is insufficient;
  per-family gates and empty-data attribution gates are required.
