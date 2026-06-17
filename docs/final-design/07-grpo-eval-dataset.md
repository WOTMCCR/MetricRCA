# MetricRCA GRPO Eval Dataset Contract

## Purpose

Every deterministic eval run must be reusable as agent-training data. The eval
runner writes the ordinary summary and per-case JSON artifacts, and also exports
a GRPO-ready trajectory dataset under:

```text
eval_out/<eval_id>/grpo_dataset/
  manifest.json
  trajectories.jsonl
```

This dataset is derived from persisted artifacts only. It does not trust graph
return state and it does not let memory become final evidence.

## Trajectory Record

Each JSONL line is one trajectory for one eval phase and case.

```json
{
  "schema_version": "metric-rca-grpo-v1",
  "dataset_kind": "metric_rca_eval_trajectory",
  "eval_id": "v3-repair-acceptance-20260617-05",
  "eval_suite": "acceptance",
  "phase": "baseline",
  "case": {
    "case_id": "C09_gmv_uv_organic_drop",
    "question": "Why did yesterday's GMV fall despite stable merchandising?",
    "tags": ["p7", "discovery"],
    "scenario_family": "regression"
  },
  "predictions": [
    {
      "case_id": "C09_gmv_uv_organic_drop",
      "aspect": "outcome",
      "prediction": {"top1_ok": true},
      "reasoning": "...",
      "risks": ["..."]
    }
  ],
  "ground_truth": {
    "metric_id": "gmv",
    "business_date": "2026-06-05",
    "expected_anomaly": true,
    "root_causes": [
      {
        "root_cause_type": "campaign_traffic_drop",
        "dimension": "channel",
        "element": "organic",
        "weight": 1.0
      }
    ]
  },
  "final_answer": {
    "status": "succeeded",
    "metric_id": "gmv",
    "selected_candidate": {"root_cause_type": "campaign_traffic_drop"},
    "evidence_ids": ["<run_id>:E1", "<run_id>:E2_channel", "<run_id>:E_select_channel", "<run_id>:E3", "<run_id>:E4", "<run_id>:E_rank"],
    "report": {}
  },
  "trajectory": {
    "run_id": "<run_id>",
    "agent_run": {},
    "trace_steps": [],
    "evidences": [],
    "sql_audit": [],
    "operation_tasks": [],
    "report": {},
    "memory_records": []
  },
  "judge": {
    "judge_name": "deterministic_ground_truth_artifact_judge",
    "reward": 1.0,
    "reward_scale": "binary_0_1",
    "reward_basis": "final_answer_matches_ground_truth_and_is_supported_by_current_run_artifacts",
    "failed_gates": [],
    "subrewards": {}
  },
  "diagnostics": {},
  "detail": {}
}
```

## Reward Rule

The training reward follows the external-judge framing: a trajectory receives
`1.0` only when the final answer is correct against ground truth and the answer
was produced through valid tools and current-run evidence. Otherwise it receives
`0.0`.

For anomaly cases, reward `1.0` requires:

```text
intent_ok = 1
anomaly_ok = 1
dominant_top1_ok = 1
evidence_coverage = 1.0
sql_safe = 1
reflection_repair_ok = 1
report_traceable_ok = 1
memory_pollution_ok = 1
```

For multi-cause cases with more than one major cause, reward `1.0` additionally
requires:

```text
weighted_explanation_coverage >= 0.85
top3_contains_all_major_causes = 1
```

For no-anomaly cases, reward `1.0` requires:

```text
intent_ok = 1
anomaly_ok = 1
no_anomaly_task_ok = 1
evidence_coverage = 1.0
sql_safe = 1
report_traceable_ok = 1
memory_pollution_ok = 1
```

This means guessed answers, hallucinated root causes, missing tool execution,
unsafe SQL, stale evidence, memory-derived conclusions, and report projection
without traceable numeric claims all receive reward `0.0`.

## Diagnostics Are Not Reward

Fields such as `root_cause_set_precision`, `sql_count`, `p95_latency_ms`,
`memory_read_seen`, and `tool_sequence` are preserved for filtering, curriculum
construction, debugging, and ablation. They are not a substitute for the binary
judge reward.

## Dataset Phases

Each eval exports both phases when present:

```text
phase=memory    memory_enabled=true prepass trajectory
phase=baseline  memory_enabled=false baseline trajectory
```

This keeps useful negative examples. For example, a trajectory where memory
overrides a user-specified discovery strategy will be stored with reward `0.0`
instead of being hidden by aggregate eval metrics.
