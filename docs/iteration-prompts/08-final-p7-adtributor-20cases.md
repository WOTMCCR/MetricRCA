# Prompt 8 - Final P7: Adtributor + net-GMV decomposition + 20-case library

> Context: P6 (deepagents core migration) is merged and ACCEPTED — the agent runs
> on real deepagents with built-in filesystem tools excluded, guards/budget/repair
> verified, and the 5 MVP eval cases are green end-to-end (thresholds_met=true).
> P7 adds the 1-month analytics depth WITHOUT touching the deterministic data path
> or the LLM/guard boundary. Branch: codex/p7-adtributor-20cases (fork from the
> merged P6 head).

```text
You are implementing Final P7 in the MetricRCA repo on a new branch
codex/p7-adtributor-20cases forked from the current P6 head.

MANDATORY PRELUDE — read and obey before touching anything:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md (mandatory post-phase review)
3. docs/final-design/ (ALL five files) — v2 design source of truth. P7 detail:
   - 02-interfaces-and-data.md §2 (RootCauseCandidate v2), §3 (Adtributor spec),
     §4 (net-GMV chain), §7 (Settings increment), §8 (error codes), §9 (20-case
     table — the binding case list)
   - 04-phase-plan.md "P7" (scope + acceptance)
4. docs/reference/decisions.md (ADL-0006 report projection, ADL-0008 deepagents)

SOURCE OF TRUTH PRIORITY
1. Current user instruction
2. AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md
3. docs/final-design/ (overrides docs/MetricRCA.md where they conflict; sync
   MetricRCA.md §12 RCA Algorithms + §10 data cases as part of this phase)
4. docs/MetricRCA.md, docs/COMPLIANCE_MATRIX.md

ENVIRONMENT — use uv; network is good; LLM creds are configured (OPENAI_API_KEY).
  uv venv .venv && uv pip install -e .
  Run project commands with PATH=.venv/bin:$PATH so the Makefile resolves python.
  deepagents is a hard dependency; do not skip-pass any test that needs it.

DO-NOT-REGRESS INVARIANTS (re-verify; these are the spine — do not weaken):
- QuerySpec -> SQLRenderer -> SQLGuard -> Repository.execute_plan is the ONLY
  metric-fact path. Adtributor consumes ALREADY-FETCHED per-element evidence; it
  must NOT issue its own SQL, read fact tables, or read anomaly_ground_truth.
- MetadataRepository -> MetricService is the only metadata path. No hardcoded
  metric / dimension / dimension-value lists in services or agent code. Adtributor
  gets its elements and per-element actual/forecast values from drilldown Evidence
  result_summaries, not from literals.
- LLM/guard boundary unchanged: LLM only selects whitelisted tools + args; the new
  adtributor_attribute tool goes through GuardMiddleware exactly like the others
  (Pydantic In/Out extra="forbid", run-scoped evidence_id, budget-counted).
- ADL-0006 projection holds: the new numeric fields (explanatory_power,
  surprise_js, multi-dim contribution) are numbers — they may appear in the report
  ONLY as numeric_claims bound to a persisted Evidence, never as free LLM text.
- Zero silent fallback: every new error code is typed and surfaced in trace.

TARGET (implement exactly the P7 scope; nothing from P8/P9):

1. Adtributor service — metric_rca/services/adtributor_service.py (deterministic,
   pure; no DB, no repository import — must pass the existing services-purity test).
   Implement per 02-interfaces §3 against the NSDI'14 definitions:
   - Explanatory power for additive metrics (gmv/net_gmv/uv):
       EP_ij = (A_ij - F_ij) / (A - F), forecast F = prev-4-same-weekday baseline mean.
   - Surprise via JS divergence: p_ij=F_ij/F, q_ij=A_ij/A,
       S_ij = 0.5*(p*log(2p/(p+q)) + q*log(2q/(p+q))); p or q = 0 stays finite by the
       formula — no special-case clamping.
   - Candidate selection: within a dimension, greedily add elements by descending
     surprise, requiring single-element EP > T_EEP, stop when cumulative EP > T_EP;
     across dimensions take the top-3 most surprising. Thresholds from Settings
     (adtributor_t_ep=0.67, adtributor_t_eep=0.10).
   - Ratio metrics (pay_cvr/refund_rate/stockout_rate/complaint_rate): compute EP on
     numerator and denominator separately and combine per §3; if the metric/dim is
     unsupported for EP, raise/return a typed ADTRIBUTOR_NOT_APPLICABLE so the agent
     falls back to single-dim drilldown — never silently produce a wrong number.
   PROOF TESTS: unit tests with hand-computed values from the paper's definitions
   (additive EP sums to 100% within a dimension; JS divergence symmetric and in
   [0,1]; greedy selection picks the documented elements at T_EP/T_EEP). A test
   that mutates thresholds and asserts the selected set changes.

2. RootCauseCandidate v2 — add fields per 02-interfaces §2 to
   metric_rca/domain/models.py: dimension_elements: list[tuple[str,str]] (default
   []), explanatory_power: float|None, surprise_js: float|None. Keep single-dim
   fields for backward compatibility. Update the ranking so that when Adtributor is
   used, contribution_score derives from EP and surprise is the cross-candidate
   tie-breaker; the single-dim v1 formula remains for non-Adtributor metrics.
   PROOF: serialization round-trip test; ranking test showing EP-driven order.

3. adtributor_attribute tool — register in the agent tool whitelist with Pydantic
   In(metric_id, dimensions: list[str] <=3, evidence_ids) / Out(ranked elements
   with EP+surprise, evidence_id). It reads prior drilldown Evidence by the given
   run-scoped evidence_ids, runs adtributor_service, and persists an Evidence
   (E_adt) with result_summary capturing the ranked candidates + EP/surprise.
   It must respect the same guard/budget/scope rules as the other tools.
   PROOF: tool test asserting evidence persistence + that it never calls
   execute_plan / reads fact tables; guard-rejection and EVIDENCE_MISSING paths.

4. net-GMV chain — extend calculate_contribution decompose_spec with net_gmv_chain
   per 02-interfaces §4: first split gmv vs refund delta contribution; the dominant
   side continues into UV*CVR*AOV (gmv side) or refund-dimension drilldown (refund
   side). Keep the existing GMV=UV*PAY_CVR*AOV approximation (no fact_traffic
   pay_orders column — do not add it). PROOF: decomposition test on seeded data for
   a refund-driven vs gmv-driven net_gmv case.

5. 20-case anomaly library — extend metric_rca/data/anomaly_injection.py +
   seed_data.py ground-truth writes to the FULL C01..C20 table in 02-interfaces §9
   (C01..C05 already exist — keep their ids/semantics). Each case writes
   anomaly_ground_truth including dimension/element. Anomaly cases inject on
   2026-06-05; no-anomaly cases (C05, C19, C20) on 2026-06-04. Fixed SEED, seed is
   idempotent (make seed rebuilds deterministically). C19/C20 are FALSE-POSITIVE
   traps (weekend seasonality / sub-threshold noise) whose ground truth is
   no_anomaly. PROOF: a seed test asserting all 20 ground-truth rows exist with the
   expected metric/dimension/element and the trap cases are labeled no_anomaly;
   idempotency test (seed twice -> identical rows).

6. Eval extension to 20 cases — extend metric_rca/evals/cases.jsonl and the scorer
   so make eval runs all 20 result-level cases reading ONLY persisted artifacts.
   Add per-case fields adtributor_used and (stub-ok, single-agent) multi_agent_path.
   C06/C07 must assert multi-element / multi-dim attribution (dimension_elements
   populated). C19/C20 must assert no_anomaly_correct (no candidate, no task, no
   drill/rank trace).

DOCS-FIRST (before implementation commits):
- Update docs/MetricRCA.md §12 (Adtributor EP/JS, net-GMV chain) and §10 (20-case
  list) to reference the v2 design; mark superseded v1 numbers as appendix, don't
  delete. Update docs/COMPLIANCE_MATRIX.md: add rows for adtributor_service,
  adtributor_attribute tool, RootCauseCandidate v2, net_gmv_chain, 20-case seed,
  20-case eval — each row names a concrete proof test and a shortcut-to-avoid.
- Commit order must show the docs/matrix commit(s) BEFORE implementation commits.

ACCEPTANCE — evidence-before-done. Paste ACTUAL command output for each:
1. PATH=.venv/bin:$PATH python -m pytest -q  -> all green, count strictly greater
   than the current P6 total (230); include the new adtributor / candidate-v2 /
   tool / net-gmv / seed-20 / eval-20 proof tests.
2. Adtributor paper-value unit-test output (the hand-computed assertions).
3. Real end-to-end eval (deepagents + real LLM + MySQL):
     PATH=.venv/bin:$PATH make up && PATH=.venv/bin:$PATH make seed && \
     PATH=.venv/bin:$PATH make eval
   Paste the eval summary JSON. REQUIRED thresholds (20-case set):
     case_total=20, intent_accuracy=1.0 (20/20), anomaly_accuracy=1.0 (20/20,
     including C19/C20 NOT flagged), top1_rate>=0.80, top3_rate>=0.90,
     sql_safe_rate=1.0, report_traceable_rate=1.0, no_anomaly_correct=true,
     thresholds_met=true.
   Run make eval TWICE and paste both summaries — both must meet thresholds
   (temperature=0; if a case flips between runs, root-cause it from the trace and
   fix the system; do NOT re-roll to luck into green).
4. 06-review-checklist section A + E scans output (zero hits), plus the
   services-purity test confirming adtributor_service imports no DB/repository.

FORBIDDEN SHORTCUTS (any = defective ship = reject):
- Adtributor reading fact tables / issuing SQL / reading anomaly_ground_truth, or
  receiving per-element values from literals instead of drilldown Evidence.
- Hardcoding dimension values (electronics/paid_ads/...) anywhere in services/agent.
- Special-casing the 20 eval questions in tool/agent code to pass eval.
- Faking/mocking the LLM in the eval or production path (test fixtures may fake the
  model only at the agent_factory boundary).
- Lowering any threshold, editing ground truth to match output, or marking tests
  xfail/skip to manufacture green.
- Letting C19/C20 over-attribute (any candidate/task on a no-anomaly trap = fail).
- Adding fact_traffic.pay_orders or otherwise changing the metric-fact DDL beyond
  what the design authorizes.

DELIVERABLE: ordered commit list (docs/matrix first, then code), each with message
+ file list, plus the pasted acceptance output above. End with an honest status:
"ALL ACCEPTANCE GREEN (2x 20-case eval)" or a labeled gap block — never claim a run
you did not execute.
```
