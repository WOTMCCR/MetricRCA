# PTV Known Issues

This file records verified defects or evaluation regressions that need a
separate optimization pass. It is intentionally factual: no proposed source
change is implied by this document.

## 2026-06-22: Merge Branch PTV Red Due Memory Gate Regression

Branch: `codex/merge-phase-c-evidence`
Evaluated commit: `bc8452105d4dd166d7dd521bd2fa2e3c87a84fc2`
PTV cycle: `cycle-20260621-2222-merge`
Round: `round-01`
Eval id: `ptv-cycle-20260621-2222-merge-round-01`
Artifact root: `eval_out/ptv/cycle-20260621-2222-merge/round-01`

### Result

The full PTV run completed and passed artifact integrity checks, but the round
is red:

```json
{
  "case_total": 46,
  "completed_case_total": 46,
  "completed_memory_case_total": 46,
  "thresholds_met": false,
  "per_family_gate": true,
  "metricrca_gates_passed": false,
  "anti_cheat_valid": true
}
```

The regression-suite behavior metrics are mostly strong:

```json
{
  "intent_accuracy": 1.0,
  "anomaly_accuracy": 1.0,
  "top1_rate": 0.934783,
  "top3_rate": 1.0,
  "top3_contains_all_major_causes_rate": 1.0,
  "root_cause_set_recall_avg": 0.992754,
  "root_cause_set_precision_avg": 0.917391,
  "weighted_explanation_coverage_avg": 0.996739,
  "evidence_coverage_avg": 1.0,
  "sql_safe_rate": 1.0,
  "report_traceable_rate": 1.0,
  "no_anomaly_correct": true
}
```

The gate failure is caused by memory treatment diagnostics inside the regression
suite:

```json
{
  "memory_disabled_top1_rate": 0.934783,
  "memory_enabled_top1_rate": 0.913043,
  "memory_hit_improvement": -0.02174,
  "memory_pollution_ok": true,
  "memory_treatment_gate": false
}
```

### Primary Defect

Memory is not polluting evidence references, but enabling memory decreases
top-1 accuracy by one case. The observed regression is:

| Case | Memory enabled | Memory disabled | Expected |
|------|----------------|-----------------|----------|
| `C27_composite_cause` | `campaign_traffic_drop/channel/organic` | `campaign_traffic_drop/channel/paid_ads` | `campaign_traffic_drop/channel/paid_ads` |

This should be treated as a memory influence/ranking defect, not as evidence
identity corruption:

- `memory_pollution_ok` is `true`.
- `evidence_coverage_avg` is `1.0`.
- `report_traceable_rate` is `1.0`.
- The selected memory-enabled candidate uses current-run evidence ids.

### Top-1 Residual Cases

The non-memory-disabled main eval has three top-1 residual cases:

| Case | Selected top-1 | Expected dominant cause | Notes |
|------|----------------|-------------------------|-------|
| `MC06_net_gmv_multi_driver` | `stockout/category/electronics` | `campaign_traffic_drop/channel/paid_ads` | Top-3 and set coverage pass; dominant ordering is wrong. |
| `IX02_gmv_interaction_discovery` | `campaign_traffic_drop/channel/paid_ads` | `interaction_channel_category/channel/paid_ads` | Interaction root-cause type is not ranked as dominant. |
| `IX03_uv_interaction_cell` | `campaign_traffic_drop/channel/paid_ads` | `interaction_channel_category/channel/paid_ads` | Same interaction-vs-campaign typing issue. |

There are no top-3 residual cases.

### Secondary Observations

The PTV gap report contains 32 divergent prediction-vs-actual gaps:

```json
{
  "total": 276,
  "accuracy": 0.8841,
  "by_divergence": {
    "correct": 244,
    "complexity_gap": 26,
    "design_flaw": 6
  }
}
```

Most execution gaps are prediction calibration misses: discovery and multi-cause
plans often use 11-24 trace steps while the conservative prediction expected 5.
These gaps do not explain the red gate directly.

Root-cause set precision is below 1.0 on several multi-cause cases because the
candidate set includes extra plausible dimensions or elements. The aggregate
precision remains above the threshold: `0.917391`.

### Recommended Next Investigation

Start with the memory-enabled path for `C27_composite_cause`:

1. Compare trace steps and ranking inputs between memory-enabled and
   memory-disabled runs.
2. Check whether memory changes the discovery lane selection, candidate
   normalization, or rank scoring weights before `E_rank`.
3. Confirm that memory-read content is only advisory and cannot override current
   evidence ranking.

Then investigate interaction root-cause typing for `IX02` and `IX03`; those are
behavioral top-1 misses but do not currently block top-3 or set coverage.
