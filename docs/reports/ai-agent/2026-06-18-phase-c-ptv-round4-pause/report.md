# Phase C PTV Round 4 Pause Report

Date: 2026-06-18
Branch: `codex/c-complex-causal`
Cycle: `eval_out/ptv/cycle-20260618-1535`

## Current State

The PTV cycle is still in progress. `meta.json` records 46 total cases, base
commit `805fda7293200012c8a02bf0815d8116462fdaea`, and status `in_progress`.

Round 1 and Round 3 have the same headline eval result: `top1_rate=0.826087`,
`top3_rate=0.891304`, `multi_cause.top3_rate=0.555556`,
`root_cause_set_precision_avg=0.676504`, `root_cause_set_recall_avg=0.916667`,
and `thresholds_met=false`.

Round 2 regressed after ranking-layer work: `top1_rate=0.782609`,
`top3_rate=0.869565`, `multi_cause.top3_rate=0.444444`,
and `root_cause_set_precision_avg=0.672079`. This supports the controller
override away from ranking-first self-lock.

Invariant gates were stable through Round 3: intent, anomaly, evidence coverage,
SQL safety, report traceability, reflection repair, memory pollution, dangerous
SQL, and no-anomaly behavior all passed. The remaining problem is quality and
coverage, especially multi-cause coverage.

## Round 3 FIX-D

Round 3 was forced to `FIX-D` by controller override:

- selected cases: `MC03_cvr_multi_signal_drop`, `MC06_net_gmv_multi_driver`
- target layer: pipeline
- allowed files: `plan_compiler.py`, `policy_registry.py`
- excluded file: `ranking.py`

The fix commit is `4a6408d5aaa34768aa0786c11de151841632b772`
(`fix(phase-c): add discovery lane pipeline`). It touched policy/compiler/gate
models and tests, not `metric_rca/runtime/ranking.py`.

Core implementation:

- `DiscoveryLane` and policy `scope_mode` were added in
  `metric_rca/business/policy_registry.py`.
- `pay_cvr` broad discovery now drills `channel` and `device` with conversion
  evidence.
- `net_gmv_drop` with explicit channel scope now compiles an
  `explicit_multi_driver` plan with lanes for:
  `channel/campaign/paid_ads`, dynamic `category/inventory`, and dynamic
  `channel/conversion`.
- `plan_compiler` lowers policy lanes into select/fetch/calculate/merge/rank
  actions.

Verification before commit:

- targeted tests: 63 related tests passed
- full suite: `579 passed, 8 skipped, 29 warnings`
- `git diff --check` passed for touched files
- `ranking.py` diff was empty

## Evidence For FIX-D

MC03 was a pure discovery miss before the fix. Round 3 selected
`conversion_drop device=desktop`, while expected causes were channel-level
conversion drops: `social` and `organic`. Its Round 3 scores were `top1=0`,
`top3=0`, `recall=0.0`, `weighted=0.0`; trace evidence only included
`E2_device`, `E_select_device`, `E3_dev_desktop`, `E4`, and `E_rank`.

MC06 was also discovery-limited. Round 3 top1 was correct on
`campaign_traffic_drop channel=paid_ads`, but `top3=0`, `recall=0.333333`,
and `weighted=0.5`; the trace had only the paid_ads channel chain and did not
collect category stockout or affiliate conversion evidence.

This is why ranking cannot be the first repair: ranking can only order
available candidates. MC03 and MC06 were missing required candidates.

## Round 4 Status

Round 4 Prediction Agent wrote
`eval_out/ptv/cycle-20260618-1535/round-04/predictions.jsonl`.
It has 276 lines: 46 cases times six aspects (`intent`, `execution`,
`evidence`, `memory`, `outcome`, `multi_cause_outcome`) and passed
`metric_rca.evals.prediction` validation.

Round 4 Eval Agent ran:

```bash
PATH=.venv/bin:$PATH make eval-stream EVAL_ID=round-04 EVAL_OUTPUT_DIR=eval_out/ptv/cycle-20260618-1535
```

The command exited `2` with
`LLM_REQUIRED_UNAVAILABLE: round-04-mem-gmv_paid_ads_drop-r3`.
No `round-04.json`, `round-04.md`, or `round-04/eval-result.json` was produced.
Therefore there is no valid Round 4 eval result and no metric claim should be
made for `4a6408d` yet.

## Subagent Review Findings

The review found real issues in `4a6408d` that should be fixed before trusting a
Round 4 eval:

1. `explicit_multi_driver` gate is too permissive. It checks only contradictory
   explicit-scope filters, so undeclared dimensions are not blocked by a lane
   allowlist.
2. Contradictory `dimension/element` and `filters` are not fail-fast rejected.
   `_explicit_scope()` prefers filters while `_explicit_dimension_element()`
   prefers the dimension/element pair.
3. The NET_GMV explicit-scope policy can also be selected for unscoped
   `net_gmv_drop`, then fail later with `DISCOVERY_LANE_SCOPE_MISSING`; policy
   selection should be scopedness-aware.
4. PAY_CVR lanes are still inferred from `required_drilldowns`; to make the
   pipeline fully explicit, PAY_CVR should declare `policy.lanes` directly.

These are strictness and pipeline-shape issues, not ranking issues.

Claude CLI review was attempted with `claude -p --model opus --effort high` in
read-only mode. It did not complete because the local CLI returned:
`You've hit your session limit · resets 6:30pm (Asia/Shanghai)`.
No substitute model was used.

## Recommended Optimization Plan

1. Harden the lane pipeline before rerunning eval.

   Add a lane allowlist to `RcaPlan` and `RunContext`, and make `ActionGate`
   permit only actions matching declared discovery lanes for
   `explicit_multi_driver`. This closes the permissive-gate gap without touching
   ranking.

2. Add fail-fast scope validation in `plan_compiler`.

   If `dimension/element` and `filters` describe the same dimension with
   different elements, raise `PlanCompilerError` before building a plan. This is
   required by the zero-fallback rule.

3. Split scoped and unscoped discovery policy lookup.

   `net_gmv_drop` explicit channel-scope rules should not be returned for
   unscoped net_gmv questions. A missing unscoped NET_GMV policy should fail
   explicitly or have its own declared lanes.

4. Make PAY_CVR lanes explicit in `policy_registry`.

   Declare two lanes, `channel/conversion` and `device/conversion`, instead of
   relying on compiler lane inference. This makes the pipeline auditable and
   aligned with the PTV diagnosis.

5. Rerun Round 4 PTV eval only after the above strictness fixes.

   Success criteria should focus on MC03 and MC06 multi-cause outcomes:
   candidate discovery, top3 major-cause containment, recall, and weighted
   coverage. Do not use top1 alone.

6. After a valid Round 4 eval, choose the next target from evidence:

   - If MC03/MC06 improve: address `IX02/C23/MC02` interaction policy next.
   - Then address `MC01/MC05/MC08` stockout-vs-campaign ordering.
   - Then handle `MC04` GMV channel conversion expressibility.
   - Precision pruning should come after recall/top1 stability.

## Artifact Index

- Cycle meta: `eval_out/ptv/cycle-20260618-1535/meta.json`
- Round 3 eval: `eval_out/ptv/cycle-20260618-1535/round-03/eval-result.json`
- Round 3 MC03 trace: `eval_out/ptv/cycle-20260618-1535/round-03/cases/MC03_cvr_multi_signal_drop.json`
- Round 3 MC06 trace: `eval_out/ptv/cycle-20260618-1535/round-03/cases/MC06_net_gmv_multi_driver.json`
- Round 3 controller summary: `eval_out/ptv/cycle-20260618-1535/round-03/optimization_summary.json`
- Round 4 predictions: `eval_out/ptv/cycle-20260618-1535/round-04/predictions.jsonl`
- Round 3 fix commit: `eval_out/ptv/cycle-20260618-1535/round-03/fix_commit.txt`
