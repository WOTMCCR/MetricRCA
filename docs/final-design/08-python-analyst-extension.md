# MetricRCA Python Analyst Extension

> Status: proposed future extension. This is not part of the current v3 repair
> acceptance gate. It may be considered only after repeated deterministic
> iterations and review show that current policy/ContributionSet/scenario
> improvements cannot reliably improve complex multi-cause performance.

## 1. Boundary

The extension adds computation capacity, not a new judge.

```text
LLM + Python sandbox = Analyst
Deterministic RCA runtime = Judge
```

The analyst may compute diagnostics and propose hypotheses. It must not decide
the final root cause, write evidence, write reports, create tasks, connect to
the production database, or bypass `QuerySpec -> SQLRenderer -> SQLGuard ->
Repository`.

## 2. Intended Use

Use this extension for cases where deterministic v3 paths are structurally too
narrow:

- multi-cause weak-signal combinations
- lagged cause/effect analysis
- pairwise or higher-order interaction scans
- residual decomposition after known factors are removed
- bootstrap or perturbation stability checks
- numeric consistency checks across E1/E2/E3/E4/E_rank

Do not use it to replace policy registry coverage, current-run evidence, or
Reflection.

## 3. AnalysisFrame

The sandbox receives a sealed, read-only data package built by the deterministic
runtime.

```python
class AnalysisFrame:
    run_id: str
    metric_id: str
    target_date: str

    target_series: list[dict]
    baseline_series: list[dict]
    dimension_frames: dict[str, list[dict]]
    signal_frames: dict[str, list[dict]]
    factor_frames: dict[str, list[dict]]

    evidence_ids: list[str]
    sql_audit_ids: list[str]
    frame_hash: str
```

`AnalysisFrameBuilder` may only read persisted current-run artifacts and
repository results produced through guarded `QuerySpec`. The frame hash is
stored with every sandbox output so PromotionValidator can reject stale or
tampered diagnostics.

## 4. SandboxHypothesis

Sandbox output is advisory and must be schema-validated.

```python
class SandboxHypothesis:
    hypothesis_id: str
    type: str  # lag | interaction | weak_signal_set | residual | stability
    candidates: list[dict]
    computed_metrics: dict[str, float]
    required_verification: list[dict]
    code_hash: str
    input_hash: str
```

Forbidden fields include direct final-answer fields such as
`selected_candidate`, `expected_element`, `expected_root_cause_type`, and
report-ready conclusion text.

## 5. Promotion Flow

Only verified hypotheses can affect canonical attribution.

```text
AnalysisFrameBuilder
  -> PythonAnalyst
  -> HypothesisStore
  -> PromotionValidator
  -> E_lag / E_interaction / E_residual / E_stability
  -> ContributionSetBuilder
  -> Reflection
  -> ReportProjector
```

`PromotionValidator` recompiles the required verification as deterministic
`QuerySpec`, executes it through SQLGuard and Repository, and persists new
evidence only when the verification passes. `ContributionSet` may reference
sandbox-derived evidence only after promotion.

Reflection must fail any final claim that is supported only by sandbox output
without promoted current-run evidence.

## 6. Computations

The first useful analyst routines are:

- Lag scan: compute best lag and lag correlation between candidate signal
  series and target metric movement.
- Weak-signal set search: find small candidate sets that materially reduce
  target residual while penalizing unnecessary complexity.
- Interaction scan: compare joint movement against an additive expectation for
  pairs such as `channel x device` or `product x warehouse`.
- Residual analysis: quantify unexplained movement after known factor graph
  terms are applied.
- Stability check: bootstrap or perturb candidate ranking and report top-k
  stability.
- Consistency check: verify that contribution totals, bad deltas, and
  percentages in current-run evidence reconcile within tolerance.

## 7. Causal Evidence Levels

Reports should avoid claiming certain causality. Candidate support is classified
as:

```text
L1 correlation: movement is aligned in time and direction.
L2 temporal: candidate signal precedes target movement.
L3 mechanism: business mechanism is supported by factor graph evidence.
L4 interventional: experiment or quasi-experimental evidence exists.
```

Most operational RCA cases should be expected to reach L2/L3, not L4. Report
language should use evidence-support phrasing, for example "verified driver" or
"evidence supports", rather than "proves causality".

## 8. Sandbox Constraints

The default implementation target is a local sandbox with:

- read-only mounted AnalysisFrame
- no network
- no database credentials
- CPU, memory, and wall-clock limits
- allowlisted Python packages
- validated output schema
- persisted code hash, input hash, stdout/stderr summary, and generated
  artifact paths

A hosted code execution environment can be used later only if all input frames,
code hashes, output summaries, and artifacts are copied back into project-owned
storage. Hosted ephemeral storage must never be treated as durable evidence.

## 9. Dataset Implications

GRPO trajectory records may include analyst diagnostics and promoted evidence
ids as auxiliary training fields. The binary external judge reward remains
unchanged: a trajectory receives `1.0` only when the final answer matches ground
truth and is supported by current-run promoted evidence. Sandbox-only guesses,
failed tools, unsafe SQL, stale frame output, or hallucinated conclusions still
receive `0.0`.
