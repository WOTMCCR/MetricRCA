# PTV to GRPO Bridge

## 1. Objective

The GRPO export is a derived training/evaluation dataset. It never changes runtime decisions, eval scores, ground truth, or PTV controller state. Records are generated only from committed PTV artifacts and artifact-grounded task trajectories.

The exporter is:

```bash
python -m metric_rca.evals.grpo_exporter
```

## 2. Three layers

### Layer 1: controller optimization context

One record is generated for each complete PTV round. It contains:

- prior and current aggregate metrics;
- gap summary and remaining diagnoses;
- RULE-C1 through RULE-C5 inputs;
- selected fix category and layer;
- commit lineage and formal confirmation state;
- deterministic reward for rule validity, diagnosis alignment, and next-round effect.

This layer trains controller-level decisions, not product RCA answers.

### Layer 2: sub-agent trajectories

Two record types are generated.

`product_task` records wrap the existing artifact-grounded `grpo_dataset/trajectories.jsonl` produced by the eval runner. They retain trace, evidence, SQL audit, report, and deterministic task score. The exporter does not recompute a more lenient judge.

`prediction` records join each prediction to its exact `gap_report.json` row. Prediction rewards distinguish:

```text
correct          eligible when reasoning cites concrete code
complexity_gap   non-positive
 design_flaw     non-positive
overfit          never eligible for a positive example
```

An overfit prediction remains in the complete dataset for negative training and analysis, but is excluded from `positive_records.jsonl`.

### Layer 3: coding-model fix trajectories

A coding-fix record links:

- diagnosis from the preceding round;
- proposed files from that diagnosis;
- exact `fix_commit` git diff;
- changed-file list;
- before and after aggregate metrics;
- before and after case results;
- `fix_effective`, `fix_minimal`, and `fix_regressed`.

The diagnosis must come from the round that requested the fix. The result must come from the later round that evaluated the fix commit.

## 3. Reward contracts

### Task reward

A product task is positive only when all fixed gates pass: intent, anomaly/no-anomaly behavior, multi-cause top3 where applicable, complete evidence, SQL safety, reflection, report traceability, and memory pollution.

### Controller reward

A controller record combines:

- controller rule validity;
- support of the selected category by diagnosis;
- verified next-round effect without aggregate regression.

### Coding-fix reward

`fix_effective=true` requires every targeted failing case to recover and no aggregate/case regression.

`fix_minimal=true` requires code changes to stay within diagnosis-proposed files. Test and documentation files are allowed evidence of verification. Unrelated production files make the fix non-minimal.

`fix_regressed=true` when a previously passing case fails or a tracked aggregate metric decreases.

A coding record is positive only when it is effective, minimal, and non-regressing.

## 4. Secret redaction

Every record is recursively redacted before validation and persistence. Redaction covers:

- API keys and provider tokens;
- GitHub tokens;
- bearer authorization values;
- password/secret fields;
- credential-bearing DSNs;
- private keys;
- inline credential assignments.

The exporter performs a second scan after redaction and fails if secret-like material remains. It does not silently omit the affected record.

## 5. Strict schema

Every record includes:

```text
schema_version
trajectory_id
layer
cycle_id
round
source
input
trajectory
output
reward
metadata
```

Layer-specific required fields are validated. Unknown layer names, missing reward components, invalid reward ranges, or missing coding-fix assessment flags are typed schema failures.

## 6. Output

```text
<cycle>/grpo_export/
  layer1_controller.jsonl
  layer2_sub_agent.jsonl
  layer3_coding_fix.jsonl
  positive_records.jsonl
  manifest.json
```

`manifest.json` records layer counts, positive count, reward histogram, redaction count, and hashes of generated files. It rejects any overfit prediction marked positive.

## 7. Command

```bash
PATH=.venv/bin:$PATH make grpo-export \
  GRPO_CYCLE_DIR=eval_out/ptv/cycle-20260618-2358
```

Optional range:

```bash
PATH=.venv/bin:$PATH make grpo-export \
  GRPO_CYCLE_DIR=eval_out/ptv/cycle-20260618-2358 \
  GRPO_FROM_ROUND=19 \
  GRPO_TO_ROUND=22
```

Layer 3 requires the referenced git commits to exist in the local repository. Missing commits or empty diffs fail instead of producing an unverifiable coding trajectory.
