# Prompt 8 - Final P7: Adtributor (in ranker) + net-GMV + 20-case (ADL-0009 revision)

> SUPERSEDES the prior draft of this file. P7's first real-LLM eval exposed that
> the agent drifts on target metric and that adtributor-as-a-tool causes
> "stops at E_adt" failures; the in-progress fixes leaked answers into the eval
> questions and risked collapsing the C07 multi-dim case. ADL-0009 sets the
> correct path. Implement THIS spec.

```text
You are implementing Final P7 in the MetricRCA repo. Branch hygiene: create a
fresh branch codex/p7-adtributor-20cases from the MERGED P6 head (commits
4c50c43 docs + 5a732d3 code). Do NOT stack P7 on top of uncommitted P6 work.
You may cherry-pick / re-apply the salvageable in-progress P7 work (see KEEP list).

RECOMMENDED CLEAN-REDO STRATEGY: the working tree may carry prior P7 in-progress
changes that include ADL-0009-violating code (adtributor_attribute wiring, answer-
leaking eval questions, single-dim C07). Rather than surgically reverting those
while keeping the good parts in-place, the safer path is:
  1. git checkout 597336c -- <REDO files>   # reset violating files to the clean
     ADL-0009 base (e.g. metric_rca/agent/tools/registry.py, deep_tools.py,
     evals/cases.jsonl, agent/prompts.py, etc.)
  2. Keep the KEEP-list files as-is (adtributor_service.py, domain/models.py,
     data/seed_data.py, etc.)
  3. DELETE metric_rca/agent/tools/adtributor_attribute.py (must not exist).
  4. Re-implement REDO items from scratch on the clean base.
This avoids partial-revert bugs where old wiring survives in an import or test.
Files likely needing reset (REDO): agent/tools/registry.py, agent/deep_tools.py,
agent/prompts.py, agent/middleware.py, agent/runner.py, agent/factory.py,
agent/tools/schemas.py, evals/cases.jsonl, services/attribution_service.py,
tests/test_tools.py, tests/test_attribution.py.
Files likely safe to keep: services/adtributor_service.py, domain/models.py,
data/seed_data.py, data/anomaly_injection.py (review C07 injection strength),
config/settings.py, agent/reflection.py, tests/test_adtributor.py,
tests/test_domain_models.py, tests/test_seed.py.

MANDATORY PRELUDE — read and obey before touching anything:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md (mandatory post-phase review)
3. docs/final-design/ (ALL five files). P7-critical:
   - 02-interfaces §2 (RootCauseCandidate v2), §3 (Adtributor — now INSIDE the
     deterministic ranker), §4 (net-GMV), §7 (Settings + model floor),
     §8 (error codes), §9 (20-case), §9.1 (EVAL INTEGRITY RULE), §9.2 (C07)
   - 01-architecture §3.1 (P7 guard increment), §4 (filesystem governance)
   - 04-phase-plan "P7"
4. docs/reference/decisions.md ADL-0009 (binding), ADL-0008, ADL-0006

ENVIRONMENT — uv; network good; LLM creds configured.
  uv venv .venv && uv pip install -e .
  Run with PATH=.venv/bin:$PATH. deepagents is a hard dep (no skip-pass).
  Eval model floor (ADL-0009): use a capable model, NOT gpt-4.1-mini. Pass
  METRIC_RCA_LLM_PROVIDER / METRIC_RCA_LLM_MODEL / METRIC_RCA_LLM_API_KEY
  explicitly to make eval; record provider+model into eval_run.summary.

KEEP (salvage from the in-progress P7 work — these are correct):
- AdtributorService as a pure service (no DB/repository import).
- RootCauseCandidate v2 fields (dimension_elements, explanatory_power, surprise_js).
- net_gmv_chain decomposition.
- seed DATA fixes: keep a few orders so anomaly slices don't SUM to NULL; lower
  baseline complaint/refund so injected anomalies are genuinely anomalous.
- v2 canonical comparison fix (validate persisted selected_candidate through
  RootCauseCandidate before Reflection equality).
- the tool-schema registration fix (but implement it via KEEP-SSOT below).

REDO / CHANGE (these in-progress fixes violate ADL-0009 — undo and replace):
1. REMOVE adtributor_attribute from the LLM tool whitelist and action space.
   Fold Adtributor INTO the deterministic rank_root_causes implementation: when
   the metric/dimension is EP-applicable, rank_root_causes calls AdtributorService
   on the per-element actual/forecast already persisted by drilldown_dimension
   Evidence, writes EP/surprise into candidates, and persists them in E_rank/E4.
   When not applicable -> ADTRIBUTOR_NOT_APPLICABLE -> single-dim path. The LLM's
   action space in P7 stays identical to P6 (no new tool to "remember to continue
   past"). AdtributorService must never read fact tables / issue SQL / read
   anomaly_ground_truth / receive literal element values.
2. RESTORE natural-language eval questions (ADL-0009 §9.1). Strip from every
   cases.jsonl question: the `metric_id=<x>` literal, the root-cause MECHANISM
   words (from stockout / because refunds increased / from UV / after a price
   change / from logistics / high-price SKU mix), and — for discovery cases
   (C06/C07/C08/C09 and any case whose test is to FIND the slice) — the
   to-be-discovered dimension/element. A user-specified slice may remain ONLY when
   that case's scenario genuinely is "user pinned this slice" and the scored answer
   is the mechanism (root_cause_type), not the slice. intent-parse accuracy must
   be a REAL scored metric on natural questions, never trivially 1.0.
3. FIX metric drift the right way (not via question text): make the intent and
   expert system prompts explicitly distinguish TARGET METRIC (the KPI the user
   asks to explain) from CAUSE MECHANISMS (stockout/refund/UV/AOV are hypotheses
   to verify, never the target). Combined with the model floor, this must hold
   intent accuracy without leaking the metric into the question.
4. C07 (ADL-0009 §9.2): make the injection produce a DOMINANT
   electronics×paid_ads cross; assert selected_candidate.dimension_elements
   contains BOTH (channel,paid_ads) AND (category,electronics). Do NOT collapse
   C07 ground truth to single-dim channel=paid_ads. If the cross can't be made
   dominant, fix seed/algorithm — never edit ground truth to match output.

NEW REQUIRED GUARDS / STRUCTURE (ADL-0009, 01-arch §3.1):
- KEEP-SSOT: derive GuardMiddleware tool_arg_schemas from the SAME tool registry
  that exposes tools; add a test asserting every whitelisted data tool has a
  registered In-schema (so "tool exposed but schema unregistered" can't recur).
- Run-level target-metric invariant: anchor the run metric to the parsed intent
  (MetricService.parse_question, whitelist-validated), NOT the first tool call.
  Any later tool passing a different metric_id -> recoverable
  METRIC_SCOPE_VIOLATION (typed, traced, no execute, no budget spend). Add the
  METRIC_SCOPE_VIOLATION code to the error table. Prove: a run where the LLM tries
  to switch metric mid-run is rejected and the evidence chain stays single-metric.

DO-NOT-REGRESS INVARIANTS (re-verify):
- QuerySpec->SQLRenderer->SQLGuard->Repository is the only metric-fact path.
- MetadataRepository->MetricService is the only metadata path; no hardcoded
  metric/dimension/dimension-value literals in services or agent code.
- ADL-0006 projection: EP/surprise and all numbers appear only as numeric_claims
  bound to persisted Evidence, never as free LLM text.
- P6 guards intact: filesystem tools excluded (real compiled-graph ToolNode proof
  test still green), recoverable-schema-invalid, budget-exempt finalizer, etc.

DOCS-FIRST (commit BEFORE implementation commits):
- Update docs/MetricRCA.md §12 (Adtributor EP/JS now inside ranker; net-GMV) and
  §10/§17 (20-case + eval integrity); mark superseded v1 as appendix.
- Update docs/COMPLIANCE_MATRIX.md: rows for AdtributorService, ranker-internal
  Adtributor, RootCauseCandidate v2, net_gmv_chain, 20-case seed, 20-case eval,
  eval-integrity rule, tool-schema SSOT, METRIC_SCOPE_VIOLATION — each names a
  proof test + shortcut-to-avoid.

ACCEPTANCE — evidence-before-done; paste ACTUAL output for each:
1. PATH=.venv/bin:$PATH python -m pytest -q -> all green, count > P6's 230.
   Include: Adtributor paper-value unit tests; ranker-invokes-Adtributor test;
   services-purity test for adtributor_service; tool-schema SSOT test;
   METRIC_SCOPE_VIOLATION test; C06 multi-element + C07 multi-dim assertions.
2. Adtributor paper-value unit-test output (hand-computed EP sums to 100% within
   a dimension; JS symmetric in [0,1]; greedy selection at T_EP/T_EEP).
3. Real end-to-end eval TWICE (deepagents + capable LLM + MySQL):
     PATH=.venv/bin:$PATH make up && PATH=.venv/bin:$PATH make seed && \
     METRIC_RCA_LLM_PROVIDER=... METRIC_RCA_LLM_MODEL=<capable> \
     METRIC_RCA_LLM_API_KEY="$OPENAI_API_KEY" PATH=.venv/bin:$PATH make eval
   Paste BOTH summary JSONs (incl. provider/model). REQUIRED (20-case):
     case_total=20, intent_accuracy=1.0 (on NATURAL questions, no metric_id=
     leak), anomaly_accuracy=1.0 (incl. C19/C20 NOT flagged), top1_rate>=0.80,
     top3_rate>=0.90, sql_safe_rate=1.0, report_traceable_rate=1.0,
     no_anomaly_correct=true, thresholds_met=true. Both runs meet thresholds;
     if a case flips, root-cause from trace and fix the system — do NOT re-roll.
4. Prove the eval did not regress to answer-leakage: paste 3-4 sample questions
   from cases.jsonl showing natural phrasing with NO metric_id= and NO mechanism
   words, AND show intent_accuracy is computed from the LLM parse (not echoed).
5. 06-review A+E scans (zero hits) + adtributor_service purity test output.

FORBIDDEN SHORTCUTS (any = reject):
- metric_id= / dimension-as-answer / mechanism words in eval questions.
- Collapsing C07 to single-dim, or editing any ground truth to match output.
- Re-introducing adtributor_attribute as an LLM tool.
- Adtributor reading fact tables / SQL / ground_truth / literal element values.
- Hardcoding dimension values anywhere in services/agent.
- Faking/mocking the LLM in eval or production paths.
- Using gpt-4.1-mini (or weaker) as the acceptance model.
- Lowering thresholds / xfail / skip to manufacture green.

DELIVERABLE: ordered commit list (docs/matrix first, then code), each with
message + file list, plus the pasted acceptance output. End with honest status:
"ALL ACCEPTANCE GREEN (2x natural-question 20-case eval)" or a labeled gap block.
```
