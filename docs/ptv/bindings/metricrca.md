# MetricRCA PTV Binding

## Binding identity

```text
project_binding: metricrca
cycle_schema: metricrca-ptv-cycle-v2
round_schema: metricrca-ptv-round-v2
```

## Prediction aspects

Every case uses:

```text
intent
execution
evidence
memory
outcome
multi_cause_outcome
```

A round is incomplete when any case/aspect pair is absent.

## Fixed product gates

PTV consumes the exact `thresholds_met`, `per_family_gate`, completion, SQL safety, evidence, traceability, anomaly, top1, top3, set recall/precision, and weighted coverage values produced by the existing eval harness. PTV does not alter those formulas.

## Commit fields

```text
eval_code_commit              code actually evaluated
fix_commit                    optimization commit included in evaluated code
post_eval_review_fix_commit   later review fix not covered by that eval
```

`fix_commit`, when present, must equal `eval_code_commit`. A later review fix starts a new confirmation sequence.

## Fix taxonomy

```text
FIX-ENUM  enum/schema mismatch
FIX-D     discovery or plan candidate-generation gap
FIX-INJ   data-injection defect
FIX-M     candidate merge/composition defect
FIX-A     attribution/ranking defect after correct candidates exist
FIX-P     intent/planning defect
FIX-T     tool/runtime/evidence contract defect
FIX-B     budget defect
FIX-S     SQL/safety defect
FIX-G     report/reflection/evidence graph defect
STRUCTURAL cross-layer redesign
NO-FIX    prediction-only divergence or non-actionable observation
```

When the correct candidate is absent from canonical contribution sets, FIX-D/FIX-M precedes FIX-A. `ranking.py` must not create discovery candidates.

## Controller rules

```text
RULE-C1  regression blocks repeating the previous category
RULE-C2  a category deferred for two rounds is promoted
RULE-C3  discovery precedes attribution when candidates are missing
RULE-C4  two aggregate regressions require explicit keep/revert
RULE-C5  one category may not be selected for three consecutive rounds
```

## Formal confirmation

Two-green confirmation requires two consecutive rounds, the same evaluated commit, the same eval contract fingerprint, no post-eval code fix, and valid anti-cheat/artifact verification.

## Memory treatment

Regression diagnostics do not prove the memory-treatment contract. Only the dedicated `memory-treatment` suite classifies memory enabled/disabled behavior. PTV summaries therefore distinguish:

```text
gate_not_applicable   dedicated experiment was not run
behavior_failure      dedicated experiment ran and failed
passed                dedicated experiment ran and passed
```

## Required commands

```bash
PATH=.venv/bin:$PATH make ptv-cycle ...
PATH=.venv/bin:$PATH make ptv-round ...
PATH=.venv/bin:$PATH make ptv-verify ...
```
