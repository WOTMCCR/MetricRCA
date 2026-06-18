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

## PTV and GRPO Training Data

PTV naturally produces three-valued training signals:

| Prediction | Actual | GRPO Reward | Training Value |
|-----------|--------|-------------|----------------|
| pass | pass | 1.0 | Standard positive |
| fail (correct reasoning) | fail | 0.0 (task failed) but **trajectory has high reasoning quality** | Teaches system modeling |
| pass | fail | 0.0 | Teaches failure recognition |
| fail | pass | 1.0 | System was better than expected — recalibrate |

The prediction reasoning, combined with the actual trajectory and
diagnosis, creates a richer training signal than raw (prompt, response,
reward) tuples. The model learns not just "what's the right answer"
but "how to reason about whether the system can produce the right
answer."

## Universality

PTV applies to any agent system with:
1. A deterministic eval harness (fixed cases + ground truth)
2. Observable execution traces (tool calls, intermediate artifacts)
3. Identifiable fix categories (what kind of change fixes what kind of failure)

The prediction schema and diagnosis format are project-specific, but
the loop structure, cognitive model, and GRPO mapping are universal.
