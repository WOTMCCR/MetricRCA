# Attribution Experience Layer

Status: active design for the PTV memory-treatment repair.

## Purpose

The attribution experience layer stores reusable analyst investigation
playbooks. It can propose generic hypotheses, prioritize already-approved
discovery lanes, and record residual-gap guidance. It is advisory and cannot
produce a final root cause.

## Hard boundary

The policy registry remains the owner of discovery coverage, signal type,
factor graph, evidence identity, and root-cause typing. The experience layer may
only reorder the complete lane set returned by that registry.

```text
ParsedIntent
    -> MetricPolicyRegistry
    -> canonical DiscoveryLane set
    -> AttributionExperienceAdvisor
    -> execution priority over the same lane set
    -> E1 / E2 / E3 / E4 / E_rank
    -> final report
```

Two orders are deliberately separate:

1. canonical lane order, owned by `MetricPolicyRegistry`;
2. execution priority, owned by `AttributionExperienceAdvisor`.

The canonical order controls `merge_contribution_sets.source_evidence_aliases`.
The execution order controls which approved evidence chain runs first. Memory
may affect only execution priority. It cannot remove a lane or change canonical
E4 source ordering.

## Configuration contract

`metric_rca/business/attribution_playbooks.yaml` may contain:

- metric and question-family selectors;
- generic dimensions and signal types;
- business-mechanism hypotheses;
- evidence branch predicates;
- candidate-retention and residual convergence guidance.

It may not contain concrete business elements, evaluation identities, target
dates, expected answers, selected candidates, or evidence identifiers. The
catalog loader rejects such fields before model validation.

## Runtime components

`metric_rca/business/attribution_experience.py` owns strict catalog models,
configuration validation, resolution, and priority-only memory influence.

`metric_rca/runtime/plan_compiler.py` first resolves the canonical policy lanes,
then applies the advisor. It generates lane actions in execution-priority order
but builds merge inputs and rank prerequisites in canonical order.

`metric_rca/runtime/plan_models.py` stores the resolved advice on `RcaPlan` for
auditing. `merge_contribution_sets` persists the same advice in canonical E4.
The advice is not consumed by contribution calculations or ranking scores.

## Interaction mechanism isolation

An interaction candidate is verified only by current-run E3 records whose
`signal_type` is `interaction`. Promotion requires supporting interaction
records for both channel and category in the target bad direction. A campaign
record for the same channel element remains a competing candidate, but it
cannot verify or replace an interaction mechanism.

## Non-goals

This change does not add conditional query pruning. Every policy-required lane
continues to run. Branch predicates are recorded for a future conditional
scheduler, but cannot suppress current-run evidence today.

This change does not alter evaluation cases, seed data, scoring thresholds, or
expected outputs.
