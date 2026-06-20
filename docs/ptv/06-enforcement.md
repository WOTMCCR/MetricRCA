# PTV Enforcement

## 1. Enforcement boundary

The PTV layer verifies optimization integrity. It must not edit or reinterpret scorer outputs, private ground truth, SQL safety, or persisted runtime evidence. Mechanical checks return typed errors and a structured `anti_cheat_report.json`.

## 2. Prediction completeness

For MetricRCA every eval case must contain six unique aspects:

```text
intent
execution
evidence
memory
outcome
multi_cause_outcome
```

The prediction case set must exactly equal the eval case set. Duplicate `(case_id, aspect)` pairs, missing risks, invalid confidence, or malformed prediction objects fail with `PTV_PREDICTION_INVALID` or `PTV_PREDICTION_INCOMPLETE`.

## 3. Code-path reasoning

Every reasoning field must cite a concrete Python file or callable. Generic statements such as “the system should pass” are not executable predictions. Excessive normalized duplication across reasoning rows fails with `PTV_TEMPLATE`.

## 4. Ground-truth leakage

Prediction reasoning must not mention private ground-truth files, the `anomaly_ground_truth` table, or phrases that disclose expected answers. When an optional private truth file is supplied, a high exact-answer overlap is not independently treated as cheating; it becomes a critical violation only when combined with private-truth references. This avoids penalizing legitimate accurate predictions while still detecting direct leakage.

## 5. Cross-round freshness

Identical prediction artifacts across optimization rounds fail with `PTV_STALE`. A formal confirmation round is the only exception because it intentionally evaluates the same code and contract again.

## 6. Commit enforcement

Optimization rounds require a distinct evaluated commit and a non-empty `fix_commit`. Confirmation rounds require the same `eval_code_commit` as the prior green round. `post_eval_review_fix_commit` is tracked separately and cannot inherit the earlier green result.

## 7. Diagnosis enforcement

Every divergent gap requires one diagnosis row with the same `(case_id, aspect)`. Missing rows fail with `PTV_NODIAG`. Diagnosis may classify a row as prediction overfit and `NO-FIX`, but it may not omit the row.

## 8. Controller enforcement

For rounds after the first, `optimization_summary.json` must contain:

```text
rule_c1_blocked_categories
rule_c2_promoted
rule_c3_discovery_priority
rule_c4_revert_assessment
rule_c5_streak_counts
```

Missing fields fail with `PTV_NORULES`. Selecting the same category for a third consecutive round fails with `PTV_STALL`.

## 9. Artifact integrity

`artifact_manifest.json` contains path, byte count, and SHA-256 for each round artifact. This is an internal machine-readable manifest, not a release checksum bundle. Verification fails when an artifact is missing or modified after finalization.

## 10. Zero fallback

The controller does not:

- continue analyst synthesis when prediction or eval fails;
- use stale predictions when prediction generation fails;
- synthesize missing diagnosis rows;
- infer missing commit lineage;
- classify a regression memory metric as a passed memory-treatment experiment;
- mark a post-review fix green without another eval;
- change scorer thresholds or ground truth.

All such conditions produce typed errors and a non-zero command exit.
