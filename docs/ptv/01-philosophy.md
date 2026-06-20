# PTV Philosophy

## What PTV Is

PTV (Predict → Test → Verify) is a cognitive diagnostic protocol for
agent system optimization. It turns eval from "run and hope" into a
structured scientific loop where every failure is explained before
it is fixed.

The core insight: **a prediction is a test of understanding, not a
statement of desired outcome**. When you predict that a case will fail,
you're asserting that you understand the system's current limitation.
When that prediction is correct, you've proven understanding. When it's
wrong, you've found a blind spot.

## Three Types of PTV Signal

```
┌──────────────────────────────────────────────────────┐
│ Signal 1: CORRECT PREDICTION OF SUCCESS              │
│   prediction=pass, actual=pass                       │
│   → System works as understood. Low learning value.  │
│                                                      │
│ Signal 2: CORRECT PREDICTION OF FAILURE              │
│   prediction=fail, actual=fail                       │
│   → You understand the system's limitations.         │
│   → Highest GRPO value: accurate system modeling.    │
│   → Diagnosis is pre-written in the prediction.      │
│                                                      │
│ Signal 3: INCORRECT PREDICTION                       │
│   prediction ≠ actual (either direction)             │
│   → Your mental model has a blind spot.              │
│   → Must update understanding before fixing code.    │
│   → divergence=overfit or divergence=complexity_gap  │
└──────────────────────────────────────────────────────┘
```

Signal 2 is the key insight. In traditional eval, a failure is just
"red" — you don't know if it was expected or surprising. In PTV, a
predicted failure is a **calibrated understanding** that guides exactly
what to fix. An unpredicted failure reveals that you were wrong about
how the system works — often more dangerous than the failure itself.

## What PTV Is Not

**PTV is not "copy ground truth into predictions and run eval."**

If predictions always match ground truth, every failure is
"unpredicted," and the diagnosis tells you nothing beyond "it didn't
work." You lose Signal 2 entirely, and GRPO training data becomes
binary (pass/fail) instead of three-valued.

**PTV is not "run eval five times hoping for a different result."**

Each PTV round requires a code change between execute phases. Re-running
identical code is noise, not signal. If nothing changed, the result
won't change. Flaky results indicate a non-deterministic system, which
is itself a finding that needs diagnosis.

**PTV is not a formality before real work.**

The prediction step is where the thinking happens. If it takes less
time to write predictions than to run eval, the predictions are too
shallow. Good predictions require reading the code path for each case
and reasoning about what will happen.

## PTV Data as Multi-layer Asset

PTV naturally produces three-valued signals that serve three layers
of consumption (see `05-grpo-bridge.md` for full definition):

| Prediction | Actual | Signal Value | Consumption |
|-----------|--------|-------------|-------------|
| pass | pass | Confirmed understanding | Layer 1: low priority; Layer 2/3: positive trajectory |
| fail (correct reasoning) | fail | Calibrated limitation | Layer 1: **highest value** — drives precise fix; Layer 2: system modeling signal |
| pass | fail | Blind spot revealed | Layer 1: must update mental model before fixing; Layer 2/3: failure recognition |
| fail | pass | Unexpected success | Layer 1: recalibrate; Layer 2/3: positive trajectory |

The **immediate** consumer is the agent system optimization loop
(Layer 1): Codex uses PTV products as structured context to decide
what to change in the multi-agent system's prompts, tools, and
policies. No model training needed — the "learning" happens in the
agent system's programmable configuration layer.

The **deferred** consumers are GRPO training pipelines (Layer 2/3):
the same data can train sub-agent models within the multi-agent
system, or future coding models, once enough cycles have accumulated.

## Universality

PTV applies to any agent system with:
1. A deterministic eval harness (fixed cases + ground truth)
2. Observable execution traces (tool calls, intermediate artifacts)
3. Identifiable fix categories (what kind of change fixes what kind of failure)

The prediction schema and diagnosis format are project-specific, but
the loop structure, cognitive model, and GRPO mapping are universal.
