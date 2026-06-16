# Prompt 11 — Phase B: Eval-Driven PTV Optimization Loop

```text
You are implementing Phase B — autonomous PTV optimization loop for the
28-case eval harness in the MetricRCA repo. Branch: codex/b-eval-optimization
from current main (must include the merged eval-harness-28cases with 28
cases.jsonl + 28 ground_truth rows).

MANDATORY PRELUDE — read and obey before touching anything:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md
3. docs/final-design/ (ALL SIX files, especially 05-phase-b-eval-optimization.md)
4. docs/reference/decisions.md (ALL ADLs)
5. metric_rca/evals/prediction.py (PTV prediction schema)
6. metric_rca/evals/gap_analyzer.py (PTV gap analysis)

ENVIRONMENT — uv; network good; LLM creds configured.
  uv venv .venv && uv pip install -e .
  Run with PATH=.venv/bin:$PATH.

════════════════════════════════════════════════════════
GOAL
════════════════════════════════════════════════════════

Reach 28/28 eval green on consecutive 2 runs by iterating PTV cycles.
Stop ONLY when the exit condition is met.

════════════════════════════════════════════════════════
ARCHITECTURE RED LINES (ABSOLUTE — VIOLATION = REJECT)
════════════════════════════════════════════════════════

1. EVAL INTEGRITY: Do NOT modify these files under ANY circumstance:
     metric_rca/data/anomaly_injection.py
     metric_rca/evals/cases.jsonl
     metric_rca/evals/scorer.py (scoring logic)
     anomaly_ground_truth rows in seed_data.py
   The eval harness is the fixed standard. You fix the system, not the test.

2. LLM-FIRST INTENT: Natural language → structured intent mapping MUST
   go through the LLM intent_planner prompt. Do NOT write Python
   keyword/regex mappers like:
     if "traffic" in question: metric_id = "uv"     # FORBIDDEN
     re.search(r"sales|revenue", question)           # FORBIDDEN
   All natural-language semantic resolution happens in the LLM prompt.
   Downstream code consumes ONLY the structured ParsedIntent output.

3. DATA PATH: QuerySpec → SQLRenderer → SQLGuard → Repository is the
   ONLY data access path. No raw SQL, no pandas, no direct engine calls.

4. METADATA PATH: metric_definition comes from DB via MetricService.
   No hardcoded metric aliases, dimension lists, or family mappings in
   services/ or agent/ runtime code.

5. ZERO SILENT FALLBACK: Every failure path must produce a typed
   error_code. No `except Exception: continue`.

6. BACKWARD COMPATIBLE: Original 20 cases must stay green EVERY round.

════════════════════════════════════════════════════════
PTV LOOP — REPEAT UNTIL EXIT CONDITION
════════════════════════════════════════════════════════

Set round=1. For each round:

──── STEP 1: PREDICT ────

Create eval_out/eval-b{round}/predictions.jsonl with 5-aspect predictions
for ALL 28 cases: intent, execution, evidence, memory, outcome.

Prediction schema (see metric_rca/evals/prediction.py):
  {"case_id": "...", "aspect": "intent|execution|evidence|memory|outcome",
   "prediction": {...}, "reasoning": "...", "confidence": 0.0-1.0,
   "risks": ["at least one"]}

Required keys per aspect:
  intent:    {"metric_id": "..."}
  execution: {"tool_sequence": [...]} or {"step_count": N} or
             {"critical_decisions": [...]}
             (no_anomaly cases MUST include "forbidden_tools")
  evidence:  {"chain": "E1→E2→E3→E4→E_rank" or "E1_only" etc.}
  memory:    {"influence": "none|planning_priority|..."}
  outcome:   {"root_cause_type": "...", "top1_ok": true/false,
              "anomaly_ok": true/false}

For round 1, predictions for original 20 cases: high confidence (>0.85),
based on known P9 eval results (all 20/20 green historically).

For round 1, predictions for new 8 cases: use these guidelines —
  C21_cvr_discovery: intent likely OK (pay_cvr); execution risk: may
    drilldown channel before device; confidence ~0.5
  C22_gmv_borderline: intent risk: "two days ago" date parsing;
    outcome: no_anomaly; confidence ~0.4
  C23_uv_organic_drop: intent HIGH RISK: "traffic" → uv mapping likely
    unsupported; confidence ~0.2
  C24_gmv_positive_spike: intent risk: "on the 2nd" date parsing;
    anomaly detection risk: positive direction; confidence ~0.2
  C25_refund_discovery: intent likely OK; execution risk: product
    discovery path; confidence ~0.5
  C26_ambiguous_intent: intent HIGH RISK: "sales" → gmv, "seems off",
    no date; confidence ~0.2
  C27_composite_cause: intent OK (same question as C06); outcome risk:
    requires paid_ads+electronics in dimension_elements; confidence ~0.3
  C28_multi_day_drift: intent risk: "since the weekend" temporal
    parsing; confidence ~0.3

For round 2+, update predictions based on previous round's actual results
and any fixes applied.

Validate: python -m metric_rca.evals.prediction eval_out/eval-b{round}/predictions.jsonl
Must exit 0 with no warnings.

──── STEP 2: EXECUTE ────

  make eval-stream EVAL_ID=eval-b{round}

Record the eval output. Note which cases passed and which failed.

──── STEP 3: VERIFY ────

  make eval-gaps EVAL_ID=eval-b{round}

Record the gap_report.md output.

──── STEP 4: CHECK EXIT CONDITION ────

Count passing cases. If ALL of these are true:
  a) 28/28 cases green (intent_ok=1, anomaly_ok=1, top1_ok=1 for anomaly
     cases; anomaly_ok=1, no_anomaly_task_ok=1 for no_anomaly cases)
  b) no_anomaly_correct=true
  c) dangerous_sql_blocked=true
  d) sql_safe_rate=1.0
  e) report_traceable_rate=1.0
  f) memory_pollution_ok=true

Then run eval ONE MORE TIME:
  make eval-stream EVAL_ID=eval-b{round}-confirm

If the second run also passes all criteria above → EXIT LOOP. Go to
FINALIZE section.

If not 28/28, continue to STEP 5.

──── STEP 5: DIAGNOSE ────

For EACH failing case, read per-case artifacts from eval_out/eval-b{round}/
and produce a diagnosis:

  | case_id | failed_field | trace_analysis | fix_type | file | change |
  |---------|-------------|----------------|----------|------|--------|

Fix types (from 05-phase-b-eval-optimization.md §B-2):
  FIX-I = intent prompt change (parse_question system prompt)
  FIX-G = guard/middleware logic change
  FIX-T = tool/service implementation change
  FIX-P = expert prompt guidance change

Diagnosis rules:
  - intent_ok=0 → always FIX-I
  - intent_ok=1, anomaly_ok=0 → check detect_anomaly output → FIX-T or FIX-G
  - intent_ok=1, anomaly_ok=1, top1_ok=0 → check trace → FIX-P or FIX-T

──── STEP 6: FIX ────

Implement the MINIMAL changes from the diagnosis. Guidelines:

INTENT FIXES (FIX-I):
  Find the parse_question / intent_planner system prompt. Add guidance:
  - Metric aliases: "traffic/visitors/UV → metric_id=uv",
    "sales/revenue/turnover → metric_id=gmv"
  - Relative dates: "resolve 'two days ago', 'on the Nth', 'since the
    weekend' against the business_today date in the run context"
  - Ambiguous intent: "if the user says something 'seems off', 'looks
    wrong', or 'is abnormal', treat as anomaly investigation for the
    most likely KPI"
  DO NOT write Python keyword matchers. The LLM does the mapping.

TOOL FIXES (FIX-T):
  Example: if detect_anomaly only flags negative deviations, fix the
  z-score comparison to use absolute value: abs(z) > z_thresh.
  Record an ADL for the behavior change.

PROMPT FIXES (FIX-P):
  Example: if rate_family discovery fails to drilldown the right
  dimension, add guidance to rate_family expert prompt about which
  dimensions to explore for which metrics.

GUARD FIXES (FIX-G):
  Only if middleware incorrectly blocks a valid tool call or fails to
  block an invalid one.

After implementing:
  make test    # ALL green, no regressions
  If test fails, fix the code issue before proceeding.

──── STEP 7: INCREMENT AND LOOP ────

  round = round + 1
  Go back to STEP 1 with updated predictions.

LOOP LIMIT: If round reaches 6 without exit condition, STOP and report
the current state as the deliverable. Do not loop forever.

════════════════════════════════════════════════════════
FINALIZE (after exit condition met)
════════════════════════════════════════════════════════

1. Record ADLs for all non-trivial system changes in
   docs/reference/decisions.md. One ADL per distinct behavior change
   (e.g., "ADL-0035: positive anomaly detection via abs(z)", "ADL-0036:
   intent prompt natural language alias expansion").

2. Write a Phase B summary table:

   | Round | Eval Score | Cases Fixed | Fix Types Applied |
   |-------|-----------|-------------|-------------------|
   | B1    | 22/28     | —           | —                 |
   | B2    | 26/28     | C21,C23,... | FIX-I, FIX-T      |
   | ...   | 28/28     | C24,...     | FIX-P              |

3. Run docs/iteration-prompts/06-review-checklist.md in full.
   Paste actual grep/scan output for sections A and E.

4. Final validation:
   make test
   make eval    (must be green)
   make eval    (second consecutive, must be green)

════════════════════════════════════════════════════════
DELIVERABLES (per round and final)
════════════════════════════════════════════════════════

Per round:
  - eval_out/eval-b{round}/predictions.jsonl
  - eval_out/eval-b{round}.json (eval results)
  - eval_out/eval-b{round}/gap_report.json
  - Diagnosis table
  - Code changes with justification

Final:
  - All per-round deliverables
  - Phase B summary table
  - ADLs in decisions.md
  - 06-review-checklist scan output
  - make test + 2x make eval output
  - List of all files changed across all rounds

════════════════════════════════════════════════════════
ACCEPTANCE CRITERIA
════════════════════════════════════════════════════════

HARD GATES (all must pass):
  - 28/28 eval green, consecutive 2 runs
  - Original 20 cases: 20/20 every round (no regressions)
  - make test: all green
  - No eval harness files modified (anomaly_injection, cases.jsonl,
    scorer.py scoring logic, ground_truth)
  - No keyword/regex parsers in Python runtime code
  - 06-review-checklist sections A and E clean
  - intent_accuracy = 28/28
  - anomaly_accuracy = 28/28
  - no_anomaly_correct = true
  - dangerous_sql_blocked = true
  - sql_safe_rate = 1.0
  - memory_pollution_ok = true

SOFT GATES (target):
  - top1_rate >= 0.85
  - top3_rate >= 0.93
  - report_traceable_rate = 1.0

If after 6 rounds the hard gates pass but soft gates don't quite meet
target, report the best achieved scores. Do NOT weaken cases or scorer.

════════════════════════════════════════════════════════
WHAT NOT TO DO
════════════════════════════════════════════════════════

- Do NOT modify eval harness files (anomaly_injection, cases.jsonl,
  scorer, ground_truth) — these are the fixed standard
- Do NOT write Python keyword/regex intent parsers — use LLM prompts
- Do NOT add raw SQL, pandas, or direct DB connections
- Do NOT hardcode metric aliases in Python code
- Do NOT weaken existing tests to make them pass
- Do NOT add speculative fixes not tied to a specific gap entry
- Do NOT skip the PTV predict step — predictions are required before
  each eval run to maintain scientific rigor
- Do NOT run more than 6 rounds — if stuck, report and stop
```
