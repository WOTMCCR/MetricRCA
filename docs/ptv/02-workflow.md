# PTV Workflow

## 1. Scope

Predict -> Test -> Verify is an optimization protocol around the fixed MetricRCA eval harness. It does not replace `metric_rca.evals.runner`, `metric_rca.evals.scorer`, the SQL safety path, or private ground truth. Its responsibilities are process isolation, artifact integrity, diagnosis coverage, controller enforcement, and formal confirmation.

The executable entrypoint is:

```bash
python -m metric_rca.evals.ptv_runner
```

## 2. Canonical artifact layout

Every artifact for a round is stored below one directory:

```text
eval_out/ptv/
  cycle-YYYYMMDD-HHMM/
    meta.json
    round-01/
      round_meta.json
      commit_lineage.json
      prediction.log
      predictions.jsonl
      eval.log
      barrier.json
      eval-result.json
      per-case/
      gap_report.json
      analyst_input.json
      analyst.log
      diagnosis.jsonl
      optimization_summary.json
      summary.json
      anti_cheat_report.json
      artifact_manifest.json
```

The existing eval runner may initially write `<eval_id>.json` and `<eval_id>/cases/`. `ptv_runner analyze` validates and copies these into the canonical names above. No artifact outside the round directory is treated as authoritative PTV evidence.

## 3. Cycle initialization

```bash
PATH=.venv/bin:$PATH make ptv-cycle \
  PTV_CYCLE_ID=cycle-20260620-1200 \
  PTV_TOTAL_CASES=46
```

`meta.json` records the branch, base commit, case count, maximum round count, and binding. Reusing a cycle id with conflicting metadata is a typed failure.

## 4. Round commit lineage

Each round records three distinct fields:

- `eval_code_commit`: exact commit executed by eval.
- `fix_commit`: optimization commit included in `eval_code_commit`; when present it must equal `eval_code_commit`.
- `post_eval_review_fix_commit`: a later commit created after the eval result. It invalidates formal green confirmation until that commit is evaluated.

This prevents a successful result at commit A from being presented as proof for a later review fix at commit B.

## 5. Parallel prediction and eval

Prediction and eval start from the same prepared round and run concurrently. The controller starts two explicit commands, writes independent logs, and terminates the peer command when either command fails. There is no sequential fallback.

The prediction command receives:

```text
METRIC_RCA_PTV_CYCLE_ID
METRIC_RCA_PTV_ROUND
METRIC_RCA_PTV_ROUND_DIR
METRIC_RCA_PTV_PREDICTIONS_PATH
```

The eval command receives:

```text
METRIC_RCA_PTV_CYCLE_ID
METRIC_RCA_PTV_ROUND
METRIC_RCA_PTV_ROUND_DIR
METRIC_RCA_PTV_EVAL_ID
```

`barrier.json` is created only after both commands exit with status zero.

## 6. Analyst barrier

After the barrier, `ptv_runner analyze`:

1. validates and canonicalizes eval output;
2. validates `predictions.jsonl` with the existing prediction contract;
3. runs `gap_analyzer.analyze_gaps` directly;
4. writes `gap_report.json`;
5. writes `analyst_input.json` containing every divergent case/aspect and required output path.

The analyst command must write `diagnosis.jsonl`. Every non-correct gap must have exactly addressable diagnosis coverage through `(case_id, aspect)`.

## 7. Controller summary

Finalization computes controller rules rather than relying on prose:

- RULE-C1 blocks the previous category when tracked aggregate metrics regress.
- RULE-C2 promotes a category deferred for two rounds.
- RULE-C3 blocks attribution-only work while a missing-candidate discovery gap exists.
- RULE-C4 requires an explicit keep/revert decision if two or more aggregate metrics regress.
- RULE-C5 rejects a third consecutive selection of the same category.

The resulting fields are written under `controller_rules_applied` in `optimization_summary.json`.

## 8. Formal two-green confirmation

Formal confirmation requires:

1. two consecutive green rounds;
2. identical `eval_code_commit` values;
3. identical eval contract fingerprint;
4. no post-eval review fix after either eval;
5. complete artifacts and valid anti-cheat reports.

A first green result is recorded as pending. A green result produced before a post-eval review fix does not confirm the later code.

## 9. Memory treatment interpretation

The regression suite may expose memory-enabled and memory-disabled rates, but it does not directly satisfy the memory-treatment gate. A false `memory_treatment_gate` field in a regression result is classified as `gate_not_applicable`, not automatically as a behavior failure.

To classify memory behavior, run:

```bash
PATH=.venv/bin:$PATH make eval-memory-treatment
```

A failure in the dedicated suite is a memory behavior issue. Absence of that suite is a gate coverage issue.

## 10. Complete round command

```bash
PATH=.venv/bin:$PATH make ptv-round \
  PTV_CYCLE_ID=cycle-20260620-1200 \
  PTV_ROUND=22 \
  PTV_EVAL_ID=ptv-cycle-20260620-1200-round-22 \
  PTV_EVAL_CODE_COMMIT=$(git rev-parse HEAD) \
  PTV_FIX_COMMIT=$(git rev-parse HEAD) \
  PTV_PREDICTION_COMMAND='python tools/write_predictions.py' \
  PTV_ANALYST_COMMAND='python tools/write_diagnosis.py' \
  PTV_SELECTED_FIX_CATEGORY=FIX-D \
  PTV_SELECTED_LAYER=policy/pipeline \
  PTV_CONTROLLER_JUSTIFICATION='RS01 lacks a separate AOV discovery lane.'
```

Both external commands are mandatory. Missing dependencies or failed commands return typed PTV errors and leave the round unconfirmed.
