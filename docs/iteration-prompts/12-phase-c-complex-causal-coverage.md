# Prompt 12 — Phase C: Complex Causal Coverage & E2E Validation

```text
You are implementing Phase C — complex causal data coverage, end-to-end
validation, and GRPO trajectory optimization for MetricRCA. Branch:
codex/c-complex-causal from current main.

MANDATORY PRELUDE — read and obey before any code change:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md
3. docs/final-design/06-v3-repair-plan.md (ALL sections)
4. docs/final-design/08-python-analyst-extension.md (escalation path)
5. docs/reference/decisions.md (ALL ADLs)
6. metric_rca/evals/scorer.py (scoring logic — READ ONLY in Iter 0-3)
7. metric_rca/evals/grpo_dataset.py (trajectory export)
8. metric_rca/data/anomaly_injection.py (current injection model)
9. metric_rca/data/seed_data.py _insert_ground_truth (current truth)

ENVIRONMENT:
  uv venv .venv && uv pip install -e .
  Run with PATH=.venv/bin:$PATH.
  MySQL must be up: make up && make seed

════════════════════════════════════════════════════════
PROBLEM STATEMENT
════════════════════════════════════════════════════════

Phase B achieved 28/28 single-cause eval green. However:

1. ALL 28 ground truth cases are single-cause. The GroundTruth.root_causes
   path and scoring metrics (root_cause_set_recall, weighted_explanation_
   coverage, top3_contains_all_major_causes) have NEVER been exercised
   with actual multi-cause data.

2. anomaly_injection.py uses independent if-chains — each condition
   multiplies independently. No scenario requires recognizing that two
   factors JOINTLY cause the anomaly.

3. No lagged causality: all injections are same-day. No T-k signal →
   T observation chain.

4. No interaction terms: no scenario where anomaly exists ONLY when
   channel=X AND category=Y simultaneously (either alone is normal).

5. No end-to-end pytest: `make test` runs only unit/contract tests with
   mocked dependencies. The only E2E path is `make eval-regression`,
   which is manual and not in CI.

6. No weak-signal/boundary sensitivity: injected anomalies use extreme
   multipliers (×0.38 = 62% drop). System behavior on ×0.82 (18% drop)
   is unknown.

7. tests/legacy_migration/ directory is required by v3 repair plan but
   does not exist.

8. Legacy cases.jsonl still on disk; LEGACY_CASES_PATH still declared.

════════════════════════════════════════════════════════
GOAL
════════════════════════════════════════════════════════

Reach ALL of these on two consecutive eval runs:
  - 28 original single-cause cases: 28/28 green (no regression)
  - 8+ new multi-cause cases: multi_cause family top3 >= 85%
  - 4+ interaction cases: interaction family top3 >= 85%
  - 2+ lagged causality cases: lagged family top1 >= 50% (advisory)
  - 2+ weak-signal boundary cases: weak_signal no false positive
  - root_cause_set_recall_avg >= 0.85 across ALL cases
  - weighted_explanation_coverage_avg >= 0.85 across ALL cases
  - top3_contains_all_major_causes_rate >= 0.90
  - E2E pytest passes with no database requirement skips
  - GRPO trajectories include multi-cause training records with
    reward=1.0 for correctly identified multi-cause sets

Stop ONLY when exit conditions are met OR escalation to Phase D
(Python Analyst sandbox) is triggered.

════════════════════════════════════════════════════════
ARCHITECTURE RED LINES (ABSOLUTE — VIOLATION = REJECT)
════════════════════════════════════════════════════════

1. EVAL INTEGRITY: Do NOT modify these files:
     metric_rca/evals/scorer.py (scoring logic)
     metric_rca/evals/grpo_dataset.py (trajectory export)
   The scorer is the fixed judge. Fix the system and data, not the judge.

2. ANOMALY INJECTION PURITY: anomaly_injection.py must remain a set of
   pure functions. No database access. No randomness. No imports beyond
   datetime. Existing injection behavior must not change — new scenarios
   are ADDITIVE only (new functions/conditions for new target dates).

3. BACKWARD COMPATIBILITY: The original 28 regression cases must stay
   green every iteration. Anomaly injections on TARGET_DATE (2026-06-05),
   BORDERLINE_DATE (2026-06-03), SPIKE_DATE (2026-06-02) must not change.

4. LLM-FIRST INTENT: No Python keyword/regex parsers. All NL → intent
   goes through LLM.

5. DATA PATH: QuerySpec → SQLRenderer → SQLGuard → Repository only.

6. GROUND TRUTH SEPARATION: New cases go in regression_public_cases.jsonl
   (question/tags only) and regression_private_ground_truth.jsonl (answers).
   Public files MUST NOT contain answer-bearing fields.

7. MULTI-CAUSE TRUTH FORMAT: New multi-cause ground truth uses
   root_causes JSON array with weights. Single-cause cases remain in
   legacy root_cause_type/dimension/element format. Both paths must
   work through _root_causes_from_row().

════════════════════════════════════════════════════════
ITERATIONS
════════════════════════════════════════════════════════

────────────────────────────────────────────────────────
ITERATION 0: CLEANUP & E2E HARNESS
────────────────────────────────────────────────────────

Implementation order:
  1. Delete metric_rca/evals/cases.jsonl. Remove LEGACY_CASES_PATH from
     runner.py. Add negative test: importing LEGACY_CASES_PATH fails.

  2. Create tests/legacy_migration/ directory. Move legacy E4 projection
     compatibility logic from ranking.py and projector.py into isolated
     test fixtures under tests/legacy_migration/. Production ranking.py
     must only read contribution_set (fail on missing).

  3. Add anomaly_ground_truth.root_causes column if not exists:
       ALTER TABLE anomaly_ground_truth ADD COLUMN root_causes JSON
         AFTER element;
     Add to schema migration list in seed_data.py.

  4. Create tests/test_e2e_smoke.py — a pytest-level E2E smoke test:
     - Requires MySQL (skip with pytest.mark.skipif if unavailable)
     - Seeds a smoke profile: make seed SEED_PROFILE=smoke
     - Runs 3 selected cases through RunService with real repository
     - Asserts: status in {succeeded, no_anomaly, failed}
     - Asserts: evidence_ids present, sql_audit rows exist
     - Does NOT assert top1_ok (E2E correctness is eval's job)

  5. Add make target:
       test-e2e: pytest tests/test_e2e_smoke.py -v --timeout=120

Gate:
  - make test passes (all unit/contract tests green)
  - make test-e2e passes (E2E smoke green with MySQL)
  - LEGACY_CASES_PATH import fails
  - tests/legacy_migration/ exists with at least one test file

────────────────────────────────────────────────────────
ITERATION 1: MULTI-CAUSE ANOMALY INJECTION
────────────────────────────────────────────────────────

Add new injection target dates for complex scenarios. Existing dates
(TARGET_DATE, BORDERLINE_DATE, SPIKE_DATE) MUST NOT change.

  MULTI_CAUSE_DATE = date(2026, 6, 1)  # Sunday before target week
  INTERACTION_DATE = date(2026, 5, 31)  # Saturday
  LAGGED_DATE = date(2026, 5, 30)       # Friday (signal injection)
  LAGGED_OBSERVE_DATE = date(2026, 6, 1)  # observed 2 days later

New injection functions in anomaly_injection.py:

  1. multi_cause_traffic_multiplier():
     On MULTI_CAUSE_DATE:
       - channel=paid_ads: uv × 0.55 (moderate traffic drop)
       - category=electronics: stockout_hours=12 (moderate stockout)
       Neither alone explains the full GMV drop — both are needed.
       Weight split: paid_ads=0.55, electronics=0.45.

  2. interaction_multiplier():
     On INTERACTION_DATE:
       - channel=paid_ads alone: uv × 0.95 (normal noise)
       - category=electronics alone: pay_user × 0.97 (normal noise)
       - channel=paid_ads AND category=electronics: uv × 0.30
         (catastrophic drop ONLY in interaction cell)
       Ground truth: interaction_channel_category, weight=1.0

  3. lagged_campaign_multiplier():
     On LAGGED_DATE (signal day, 2 days before observation):
       - channel=social: campaign spend × 0.15, clicks × 0.10
     On LAGGED_OBSERVE_DATE (observation day):
       - channel=social: uv × 0.35 (delayed effect manifests)
       Ground truth: campaign_traffic_drop, channel=social

  4. weak_signal_multiplier():
     On MULTI_CAUSE_DATE:
       - channel=affiliate: uv × 0.82, pay_user × 0.85
         (18% UV drop + 15% conversion drop = ~30% GMV impact,
          but neither crosses anomaly threshold individually)
       Only detectable when combined signal is considered.

Implementation order:
  a. Add new date constants
  b. Add new pure functions (no existing function touched)
  c. Hook new functions into seed_data.py order generation for
     new dates ONLY (guard: if business_date in new_dates_set)
  d. Unit test each injection function: deterministic, correct multiplier

Gate:
  - make test passes
  - New injection functions are pure (no DB, no random)
  - Existing TARGET_DATE/BORDERLINE_DATE/SPIKE_DATE multipliers unchanged
  - Smoke eval: make seed && make eval-regression still 28/28 green

────────────────────────────────────────────────────────
ITERATION 2: MULTI-CAUSE GROUND TRUTH & EVAL CASES
────────────────────────────────────────────────────────

Add 16+ new cases to regression_public_cases.jsonl and corresponding
ground truth to seed_data.py _insert_ground_truth and
regression_private_ground_truth.jsonl.

  MULTI-CAUSE CASES (8 minimum):

  MC01_gmv_traffic_and_stockout:
    question: "Why did GMV drop on Sunday?"
    tags: ["multi_cause", "campaign", "inventory", "discovery"]
    ground_truth:
      business_date: 2026-06-01
      metric_id: gmv
      expected_anomaly: true
      root_causes: [
        {"root_cause_type":"campaign_traffic_drop","dimension":"channel",
         "element":"paid_ads","weight":0.55},
        {"root_cause_type":"stockout","dimension":"category",
         "element":"electronics","weight":0.45}
      ]

  MC02_gmv_three_way_drop:
    question: "Sunday's sales were very poor, what happened?"
    tags: ["multi_cause", "campaign", "conversion", "discovery"]
    ground_truth: 3 root causes with weights 0.45/0.30/0.25

  MC03_net_gmv_refund_and_traffic:
    question: "Why did net revenue fall on Sunday?"
    tags: ["multi_cause", "campaign", "refund_quality"]
    ground_truth: 2 root causes

  MC04_gmv_weak_set:
    question: "Were there any issues with Sunday's GMV?"
    tags: ["multi_cause", "weak_signal", "discovery"]
    ground_truth: 2 weak root causes, neither dominant alone

  MC05-MC08: Additional multi-cause combinations across metrics

  INTERACTION CASES (4 minimum):

  IX01_gmv_channel_category_interaction:
    question: "Why did Saturday's GMV fall? Is it related to electronics
     on paid ads?"
    tags: ["interaction", "cross_dimension", "specified_slice"]
    ground_truth:
      business_date: 2026-05-31
      metric_id: gmv
      expected_anomaly: true
      root_causes: [
        {"root_cause_type":"interaction_channel_category",
         "dimension":"channel","element":"paid_ads","weight":1.0}
      ]

  IX02_gmv_interaction_discovery:
    question: "Why did Saturday's GMV underperform?"
    tags: ["interaction", "cross_dimension", "discovery"]
    (same truth but discovery path, no hint in question)

  IX03-IX04: Interaction across different dimension pairs

  LAGGED CASES (2 minimum):

  LG01_gmv_lagged_social:
    question: "Why did Sunday's GMV from social channels drop?"
    tags: ["lagged", "campaign", "specified_slice"]
    ground_truth:
      business_date: 2026-06-01
      metric_id: gmv
      expected_anomaly: true
      root_cause_type: campaign_traffic_drop
      dimension: channel
      element: social

  LG02_uv_lagged_social_discovery:
    question: "Why was traffic down on Sunday?"
    tags: ["lagged", "campaign", "discovery"]

  WEAK-SIGNAL CASES (2 minimum):

  WK01_gmv_weak_affiliate_boundary:
    question: "Did affiliate channel have issues on Sunday?"
    tags: ["weak_signal", "boundary", "specified_slice"]
    (expected_anomaly depends on detection threshold — decide based on
     actual z-score vs baseline)

  WK02_gmv_no_anomaly_weak:
    question: "Was Sunday's affiliate GMV normal?"
    tags: ["weak_signal", "no_anomaly"]

Implementation order:
  a. Add ground truth rows to _insert_ground_truth with root_causes JSON
  b. Add cases to regression_public_cases.jsonl (question + tags only)
  c. Add corresponding ground truth to regression_private_ground_truth.jsonl
  d. Update seed_data.py ground truth row count assertion
  e. Contract test: public cases have no answer-bearing fields
  f. Contract test: ground truth with root_causes parses correctly

Gate:
  - make test passes
  - make seed regenerates with new ground truth (no schema errors)
  - Public case file has no answer-bearing fields
  - Ground truth cases with root_causes JSON parse correctly
  - Original 28/28 regression eval still green

────────────────────────────────────────────────────────
ITERATION 3: RUNTIME MULTI-CAUSE ATTRIBUTION
────────────────────────────────────────────────────────

The deterministic runtime must be able to produce ContributionSet with
multiple candidates that collectively explain the anomaly. Currently:
  - PlanCompiler broad path produces ONE drilldown chain
  - calculate_contribution produces ONE selected_candidate
  - rank_root_causes re-ranks but the pipeline is single-thread

Changes required:

  1. PlanCompiler: for metrics with multi-dimensional discovery policy,
     compile PARALLEL drilldown chains. E.g., for GMV broad:
       E1 → E2_channel → E_select_channel → E3_channel → E4_channel
       E1 → E2_category → E_select_category → E3_category → E4_category
     E4 contribution_set merges candidates from both chains.
     Guard: if explicit_scope is set (question names a specific slice),
     keep the single-chain path.

  2. ContributionSetBuilder: new module that merges E4 payloads from
     multiple chains into a single ContributionSet:
       - candidates from all chains, deduplicated by (dimension, element)
       - selected_candidate = highest eng_confidence
       - evidence_ids = union of all chains' evidence
       - factor_graph merges per-chain factor decompositions

  3. rank_root_causes reads merged ContributionSet. Adtributor operates
     on the merged set (already supports multi-dimensional elements).

  4. Reflection: add cross-chain consistency check. If chain_A says
     paid_ads explains 80% but chain_B says electronics explains 75%,
     and sum > 110%, flag CONTRIBUTION_OVERLAP_WARNING (warning, not
     error — real multi-cause can sum > 100% due to interaction).

  5. Scorer already handles multi-cause — no scorer changes needed.

Gate:
  - make test passes
  - Single-cause 28/28 still green (no regression)
  - Multi-cause MC01: root_cause_set_recall >= 0.5 (at least one cause
    found in first PTV round — full convergence in Iteration 4)

────────────────────────────────────────────────────────
ITERATION 4: PTV OPTIMIZATION LOOP
────────────────────────────────────────────────────────

PTV = Predict → Test → Verify. This is the CORE optimization loop.

**AUTHORITATIVE SPEC: docs/ptv/**

All PTV rules, schemas, anti-pattern detection, subagent dispatch
patterns, artifact isolation layout, and GRPO bridge are defined in
the `docs/ptv/` directory. This section provides the MetricRCA-specific
execution instructions. Do NOT redefine PTV rules here — if a conflict
exists, `docs/ptv/` wins.

REQUIRED READING before executing this iteration:
  - docs/ptv/01-philosophy.md          — what PTV is (and is not)
  - docs/ptv/02-workflow.md            — loop structure + artifact isolation
  - docs/ptv/03-prediction-protocol.md — prediction rules R1-R5
  - docs/ptv/04-diagnosis-protocol.md  — diagnosis schema + fix categories
  - docs/ptv/06-enforcement.md         — anti-cheat detection rules
  - docs/ptv/bindings/metricrca.md     — MetricRCA-specific config

════ EXECUTION ════

1. Create a new PTV cycle directory:
     mkdir -p eval_out/ptv/cycle-$(date +%Y%m%d-%H%M)

2. Follow the loop in docs/ptv/02-workflow.md:
     - Use 3-agent parallel dispatch (Prediction Agent, Eval Agent,
       PTV Analyst Agent) as described in "Codex Subagent Dispatch Pattern"
     - All artifacts go into eval_out/ptv/cycle-{id}/round-{N}/
     - Every round writes: predictions.jsonl, eval-result.json,
       gap_report.json, diagnosis.jsonl, ptv_trajectory.jsonl

3. MetricRCA exit conditions (from bindings/metricrca.md):
     a) Original 28 single-cause: 28/28 green
     b) Multi-cause family: top3_rate >= 0.85
     c) root_cause_set_recall_avg >= 0.85
     d) weighted_explanation_coverage_avg >= 0.85
     e) top3_contains_all_major_causes_rate >= 0.90
     f) All safety invariants = 1.0
     g) Interaction cases: top3_rate >= 0.85
     h) No regression in any per-family gate
     i) Confirmed by 2 consecutive green runs

4. Escalation after 6 rounds with STRUCTURAL diagnosis:
     → Write eval_out/ptv/cycle-{id}/escalation.json
     → Record ADL in docs/reference/decisions.md
     → Do not implement Phase D sandbox in this session

────────────────────────────────────────────────────────
ITERATION 5: PYTHON ANALYST SANDBOX (CONDITIONAL)
────────────────────────────────────────────────────────

Execute ONLY if Iteration 4 escalation is triggered.
Reference: docs/final-design/08-python-analyst-extension.md

Implementation order:

  1. AnalysisFrame builder:
     - Read-only extraction from current-run persisted artifacts
     - Sealed frame with frame_hash
     - Unit tests: frame contains correct evidence, no DB creds

  2. Lag scan computation:
     - Cross-correlate candidate signal series with target metric
       across T-1 to T-7 day window
     - Output: SandboxHypothesis with type="lag", best_lag, correlation
     - Must produce result within 5s CPU budget

  3. Interaction scan computation:
     - For each dimension pair (d1, d2), compute:
       actual_drop(d1=x, d2=y) vs expected_drop(d1=x) + expected_drop(d2=y)
     - If |actual - expected_additive| > threshold → interaction signal
     - Output: SandboxHypothesis with type="interaction"

  4. PromotionValidator:
     - Convert SandboxHypothesis.required_verification to QuerySpec
     - Execute through SQLGuard + Repository
     - Persist promoted evidence as E_lag / E_interaction
     - Promote to ContributionSet only on verification pass

  5. Integration with PlanExecutor:
     - After standard E4, check if case has unresolved residual
     - If residual > 20%, invoke AnalysisFrame → PythonAnalyst
     - Merge promoted hypotheses into ContributionSet

  6. Resume PTV loop from Iteration 4 STEP 1 with sandbox enabled.

Gate:
  - Sandbox outputs schema-validate
  - No sandbox output directly in final report without promotion
  - Promoted evidence has current-run evidence_ids (not sandbox IDs)
  - Lagged case LG01: top1_ok=1 with lag evidence
  - Interaction case IX01: top1_ok=1 with interaction evidence
  - Original 28/28 unaffected

────────────────────────────────────────────────────────
ITERATION 6: FINAL GRPO TRAJECTORY EXPORT & CI GATE
────────────────────────────────────────────────────────

  1. Run make eval-regression with full case set (28 + new cases).
     Record GRPO trajectories including multi-cause training data.

  2. Verify GRPO trajectory quality:
     - Multi-cause cases with reward=1.0 have root_causes in ground_truth
     - judge.failed_gates is empty for reward=1.0 trajectories
     - weighted_explanation_coverage subreward reflects coverage
     - All trajectories have valid schema_version

  3. Add CI integration:
     - make test-e2e runs 3-case E2E smoke (requires MySQL)
     - Makefile target: eval-ci that runs eval-regression and asserts
       thresholds_met=true
     - Document in README: CI requires make up + make seed before
       make eval-ci

  4. Write final eval report to eval_out/reviews/phase-c-final.json:
     - Per-case scores for all cases (original + new)
     - Per-family breakdown
     - GRPO manifest
     - Sandbox usage (if Phase D was triggered)
     - Comparison vs Phase B baseline

Gate:
  - make test passes
  - make test-e2e passes
  - make eval-regression: thresholds_met=true on 2 consecutive runs
  - GRPO trajectories export with correct schema
  - All new cases have reward >= 0.0 (no schema crash)
  - Multi-cause cases: at least 85% have reward=1.0
  - README documents new eval scope

════════════════════════════════════════════════════════
VALIDATION COMMANDS
════════════════════════════════════════════════════════

Existing (unchanged):
  PATH=.venv/bin:$PATH make test
  PATH=.venv/bin:$PATH make seed SEED_PROFILE=regression
  PATH=.venv/bin:$PATH make eval-regression
  PATH=.venv/bin:$PATH make seed SEED_PROFILE=acceptance ALLOW_DESTRUCTIVE_SEED=true
  PATH=.venv/bin:$PATH make eval-acceptance

New:
  PATH=.venv/bin:$PATH make test-e2e
  PATH=.venv/bin:$PATH make eval-ci

════════════════════════════════════════════════════════
CODEX DISPATCH INSTRUCTIONS
════════════════════════════════════════════════════════

This prompt is designed for Codex autonomous execution. Each iteration
is a self-contained work unit with a clear gate.

Dispatch sequence:
  1. Create branch: codex/c-complex-causal from main
  2. Copy this file to the branch root as CODEX_PROMPT.md
  3. Run codex with: codex --prompt CODEX_PROMPT.md
  4. Codex executes Iteration 0 → gate check → Iteration 1 → ...
  5. At each gate, Codex runs the specified commands and checks output
  6. If gate fails, Codex fixes before proceeding
  7. Iteration 4 PTV loop runs autonomously until exit or escalation
  8. If Phase D escalation triggers, Codex implements Iteration 5
  9. Final gate: 2 consecutive eval runs all green + GRPO export

Codex session structure:
  Session 1: Iterations 0-1 (cleanup + injection)
  Session 2: Iterations 2-3 (cases + runtime changes)
  Session 3: Iteration 4 (PTV loop, may need multiple sessions)
  Session 4: Iteration 5 (only if escalated)
  Session 5: Iteration 6 (final export + CI)

Adversarial review handoff:
  After each Codex session completes, Claude reviews:
  a. git diff codex/c-complex-causal...main (full diff)
  b. Cross-reference against this spec: every gate condition
  c. Run make test independently
  d. Read eval output artifacts
  e. Check for violations of red lines (especially: scorer modified?
     public cases leaked? keyword parser added? injection purity broken?)
  f. If violations found → reject session, create fix prompt
  g. If clean → approve, merge, dispatch next session

════════════════════════════════════════════════════════
FINALIZE
════════════════════════════════════════════════════════

When exit conditions are met:

  1. Run: make eval-regression > eval_out/reviews/phase-c-final.txt
  2. Update docs/COMPLIANCE_MATRIX.md with multi-cause coverage
  3. Update docs/MetricRCA.md scenario coverage section
  4. Commit all changes with message:
       feat: phase c — multi-cause causal coverage and E2E validation
  5. Create PR against main

════════════════════════════════════════════════════════
ADL TEMPLATE FOR DECISIONS DURING THIS PHASE
════════════════════════════════════════════════════════

Record in docs/reference/decisions.md (newest first):

  ## ADL-C{N}: {title}
  **Date:** {YYYY-MM-DD}
  **Status:** accepted
  **Context:** {what problem, which iteration/PTV round}
  **Decision:** {what was decided}
  **Alternatives considered:** {what was rejected and why}
  **Consequences:** {what changes, what risks remain}
```
