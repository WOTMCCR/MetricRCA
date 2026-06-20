# Phase C PTV Round 20 Handoff

Date: 2026-06-20
Branch: `codex/c-complex-causal`

## Executive Summary

Round 20 did not produce a valid gate result because eval aborted during the memory prepass on `C24_gmv_positive_spike`. This was not a ranking regression. The blocking failure was a tool/runtime contract issue: a generated interaction selection alias made the persisted `evidence_id` exceed the schema limit, and the executor reported the symptom (`TOOL_SQL_COUNT_MISMATCH`) before the real typed persistence failure.

The Round 20 fix is committed and pushed-ready:

- Code fix: `32454360bb1191f18fcdc1f6dcb56f3538ba9c77`
- Round 20 artifacts: `1429bf4`
- `tmp` reference projects: `890267d`

The next work should start a new PTV round. Round 20 should not be treated as gates-passed because baseline scoring completed `0/46`.

## Evidence

PTV artifacts:

- `eval_out/ptv/cycle-20260618-2358/round-20/predictions.jsonl`
- `eval_out/ptv/cycle-20260618-2358/round-20/eval-result.json`
- `eval_out/ptv/cycle-20260618-2358/round-20/gap_report.json`
- `eval_out/ptv/cycle-20260618-2358/round-20/diagnosis.jsonl`
- `eval_out/ptv/cycle-20260618-2358/round-20/ptv_trajectory.jsonl`
- `eval_out/ptv/cycle-20260618-2358/round-20/optimization_summary.json`
- `eval_out/ptv/cycle-20260618-2358/round-20/external_review_claude_opus_high.md`
- `eval_out/ptv/cycle-20260618-2358/round-20/fix_commit.txt`

Round 20 facts:

- Prediction agent wrote 276 rows: 46 cases x 6 aspects, including `multi_cause_outcome`.
- Eval agent seeded successfully, then aborted with exit status `2`.
- Eval summary: `configured_case_total=46`, `completed_memory_case_total=23`, `completed_case_total=0`, `complete=false`.
- Before abort, memory cases through C23 were green; C07 selected `paid_ads`, supporting that Round 19 routing worked on the memory-enabled path.
- Fatal run: `ptv-cycle-20260618-2358-round-4c1db5ea-r2`.
- Fatal trace: seq 18, action `select_signal_element`, `signal_type=interaction`, alias `E_select_ch_interaction`, declared `sql_count=0`, authoritative SQL audit delta `2`.
- Schema fact: `evidence.evidence_id` is `VARCHAR(64)`.
- Failing full evidence id length: `65`.

## Root Cause

There were two coupled issues:

1. The policy-generated alias `E_select_ch_interaction` was too long when combined with the eval run id: `ptv-cycle-20260618-2358-round-4c1db5ea-r2:E_select_ch_interaction`.
2. `RcaPlanExecutor` checked SQL-count mismatch before failed-tool typed errors. The real persistence failure was therefore masked as `TOOL_SQL_COUNT_MISMATCH`.

Claude review described this as a diagnostic inversion. The deeper architecture smell remains that `evidence_id = f"{run_id}:{alias}"` couples two independently growing namespaces into one fixed-width column.

## Fix

Changed files:

- `metric_rca/agent/evidence_aliases.py`
- `metric_rca/business/policy_registry.py`
- `metric_rca/agent/tools/select_signal_element.py`
- `metric_rca/runtime/plan_compiler.py`
- `metric_rca/runtime/plan_executor.py`
- `tests/test_runtime_plan.py`
- `tests/test_runtime_plan_executor.py`
- `tests/test_runtime_tool_executor.py`
- `tests/test_business_signal_policy.py`
- `tests/test_runtime_action_gate.py`
- `docs/reference/decisions.md`

Behavior changes:

- Shortened GMV multisignal aliases while keeping dimension prefixes:
  - `E_select_ch_conversion` -> `E_select_channel_conv`
  - `E_select_ch_interaction` -> `E_select_channel_int`
  - `E_select_cat_interaction` -> `E_select_category_int`
  - `E4_channel_interaction` -> `E4_channel_int`
  - `E4_category_interaction` -> `E4_category_int`
  - `E3_ch_interaction` -> `E3_ch_int`
  - `E3_cat_interaction` -> `E3_cat_int`
- Added `MAX_EVIDENCE_ID_LENGTH` and `evidence_alias_fits`.
- Added plan compiler fail-fast validation: `EVIDENCE_ID_TOO_LONG`.
- Changed executor precedence so failed-tool typed errors win over SQL-count mismatch, while trace still records `declared_sql_count` and authoritative `sql_audit_delta`.
- Preserved `select_signal_element` SQL count on post-query persistence failures.

This is intentionally a FIX-T/tool-runtime fix. It does not modify `ranking.py`.

## Verification

Commands run:

- `PATH=.venv/bin:$PATH python -m pytest tests/test_runtime_plan.py tests/test_runtime_tool_executor.py tests/test_business_signal_policy.py tests/test_runtime_plan_executor.py tests/test_runtime_action_gate.py -q`
  - Result: `89 passed`.
- `PATH=.venv/bin:$PATH make seed && PATH=.venv/bin:$PATH make test`
  - Result: `626 passed, 8 skipped, 29 warnings`.
- `git diff --check` on changed files
  - Result: passed.
- Diff no-fallback scan
  - Result: no matches.
- PTV artifact JSON/JSONL validation
  - Result: passed.

Reviews:

- Subagent review: `Blocking findings: []`.
- Claude Opus high review: `Blocking findings: []`.

Important non-blocking review notes:

- `MAX_EVIDENCE_ID_LENGTH=64` duplicates schema knowledge from `metric_rca/data/schema.sql`; centralize or assert it later.
- There are still two alias sources: explicit policy aliases and `e3_alias_for_signal_lane()`. The compiler guard prevents silent overflow, but alias allocation should be unified.
- `ToolResult.sql_audit_delta` remains underused after executor moved to authoritative audit deltas.
- The evidence id model itself is still coupled; consider surrogate/hash ids or separate `(run_id, alias)` fields.

## tmp Reference Projects

The `tmp` examples were committed in `890267d`:

- `tmp/data-agent`
- `tmp/dbmock`
- `tmp/insight-agent`

Before committing, one real-shaped OpenRouter key in `tmp/data-agent/conf/app_config.yaml` was replaced with `change-me`. Remaining `123321` values are local DB example passwords.

Architecture lessons from `tmp` that are relevant:

- `tmp/insight-agent` has a cleaner app boundary around agent, tools, repositories, config, and frontend. It is useful as a reference for separating runtime orchestration from app surfaces.
- It does not solve MetricRCA's evidence-ledger problem directly. Do not copy it wholesale.
- The useful design direction is a clearer pipeline: intent/plan -> evidence-producing tools -> contribution sets -> ranking/reflection/report, with alias/evidence identity handled centrally.

## Current Project Status

What is improved:

- Round 19 C07 routing fix appears to hold in Round 20 memory prepass; C07 selected `paid_ads`.
- Round 20 fatal cause is now fixed at the tool/runtime layer with tests.
- Alias overflow now fails fast during plan compilation rather than mid-run persistence.
- Failed tool errors are no longer masked by SQL accounting mismatch.

What is not yet proven:

- MetricRCA gates have not passed after this fix.
- C24 has not been re-run in a full PTV round after the code commit.
- Remaining attribution precision/FIX-A backlog is not remeasured because Round 20 had `completed_case_total=0`.
- Two consecutive green evals have not been achieved.

## Recommended Next Steps

1. Start Round 21 using the same PTV protocol.
2. Prediction agent must again write all 6 aspects, including `multi_cause_outcome`.
3. Eval agent should run `make eval-stream` into `eval_out/ptv/cycle-20260618-2358/round-21/`.
4. Analyst must check whether C24 now completes and whether the previous Round 19 blocker shifts back to per-family adtributor precision.
5. If eval completes but gates fail, follow RULE-C from the latest docs. Do not return to ranking unless diagnosis is gate-valid and not blocked by a more fundamental discovery/tool issue.
6. If a new fatal appears, treat it as FIX-T unless evidence shows business logic failure.

## GPT Pro Review Prompts

Ask GPT Pro to review these questions:

- Is `evidence_id = run_id:alias` the right identity model, or should MetricRCA store a surrogate evidence id plus separate `run_id` and `alias` fields?
- Should `evidence.evidence_id` be widened to `VARCHAR(128)`, replaced by a hash, or kept at 64 with stronger run-id bounds?
- How should alias allocation be centralized so `policy_registry` and `evidence_aliases.py` cannot drift?
- Should executor error precedence be formalized as an explicit ladder, not line-order behavior?
- Should `ToolResult.sql_audit_delta` be removed, repurposed, or made authoritative only for in-memory repositories?
- Does the pipeline need a clearer intermediate object between discovery lanes and contribution sets to avoid repeated policy patches?
- After Round 21, does remaining failure support FIX-A, FIX-D, or a broader pipeline refactor?

