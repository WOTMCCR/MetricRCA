# Declarative Scenario Data Generation

## Purpose

The scenario compiler generates deterministic, answer-separated business datasets for coverage expansion and future PTV cycles. It does not replace the current MySQL regression seed and does not change the fixed 46-case judge.

The current `make seed` behavior remains unchanged for `smoke`, `regression`, `acceptance`, and `stress`. `make seed SEED_PROFILE=scenario` explicitly selects the artifact generator and writes under `eval_out/generated_data/`.

## Contracts

The generator has five strict layers:

1. `dimension_catalog.py` validates the declared dimension domain and product relationships.
2. `baseline_generator.py` creates reproducible wide business rows using a local hash-based random source. Secondary dimension membership is stable across dates; date-specific noise remains deterministic.
3. `shock_composer.py` applies selector-based operations. It contains no `case_id` branches and cannot access a database.
4. `metric_deriver.py` derives GMV, net GMV, conversion, AOV, refund, stockout, complaint, and campaign ROI metrics from row primitives.
5. `data_quality_validator.py` verifies metric identities, target-versus-baseline direction, negative controls, ground-truth evidence chains, required dimensions, required shocks, and public/private answer separation.

Every scenario declares:

- a target metric and date;
- four previous same-weekday baseline offsets;
- one or more typed shocks;
- weighted expected root causes;
- an expected evidence chain for each cause;
- one or more negative controls;
- a minimum effect boundary.

## Output

For a scenario set named `phase_c_full`, the generator writes:

```text
eval_out/generated_data/scenario/phase_c_full/
  baseline.jsonl
  catalog_snapshot.json
  scenario_spec_snapshot.json
  eval_cases_public.jsonl
  eval_cases_private_ground_truth.jsonl
  eval_case_manifest.json
  data_quality_report.json
  manifest.json
  scenarios/<scenario_id>/observations.jsonl
  scenarios/<scenario_id>/ground_truth.json
```

Public cases contain only `case_id`, `question`, and `tags`. Answer-bearing fields are private.

## Commands

```bash
PATH=.venv/bin:$PATH make scenario-generate
PATH=.venv/bin:$PATH make seed SEED_PROFILE=scenario
```

To select another deterministic seed:

```bash
PATH=.venv/bin:$PATH make scenario-generate SEED=20260607 SCENARIO_PROFILE=scenario-seed-20260607
```

## Production boundary

The wide artifact warehouse covers channel, campaign, category, product, device, geo, shop, brand, warehouse, logistics provider, payment type, membership segment, price band, promotion type, and landing page. The current production SQL renderer and MySQL fact schema do not yet expose every one of those dimensions. Promoting a generated scenario into the fixed regression suite therefore requires a separate implementation cycle that adds metadata, physical schema, renderer support, guard allowlists, policy lanes, tests, and a fresh PTV round. The generator must not be wired directly into runtime RCA as a bypass.
