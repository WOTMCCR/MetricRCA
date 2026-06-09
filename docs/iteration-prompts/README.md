# MetricRCA Iteration Prompts

This directory freezes the post-Phase-1 Codex iteration prompts for completing
MetricRCA without shortcut implementations.

The content is based on the GPT Pro review from the in-app ChatGPT `Pro /
advanced` mode, with small Codex-local hardening added for issues observed in
the current repository:

- persisted Evidence IDs must be globally unique while preserving per-run
  aliases E1-E4;
- baseline rendering is fixed to one deterministic same-weekday `IN` query;
- algorithm services must stay pure and must not import database access code;
- tool semantics and guard-rejection paths are made explicit;
- `unittest discover` is treated as a ResourceWarning smoke, while `make test`
  remains the full test gate.

## Files

- `00-global-iteration-rules.md` - paste at the top of every phase prompt.
- `00-goal-mode.md` - Goal Mode objective. Use it as the long-running guardrail,
  not as a request to implement all phases at once.
- `01-matrix-p2-services-tools.md` - Matrix P2: services, tools, evidence.
- `02-matrix-p3a-langgraph-react.md` - Matrix P3 Part A: graph, ReAct, trace.
- `03-matrix-p3b-reflection-memory.md` - Matrix P3 continuation: Reflection and
  Memory.
- `04-matrix-p4-api-ui.md` - Matrix P4: FastAPI and Streamlit.
- `05-matrix-p5-eval-docs.md` - Matrix P5: eval, README, architecture, final
  compliance.

## Recommended Use

1. Start Goal Mode with `00-goal-mode.md`.
2. For each phase, paste or reference `00-global-iteration-rules.md` first.
3. Then execute one phase at a time using the corresponding phase prompt.
4. Treat `00-global-iteration-rules.md` as mandatory for every phase, even
   when the phase prompt repeats some of the same rules locally.
5. Do not continue to the next phase unless the current phase final response
   reports `Known shortcuts: []` and maps satisfied/deferred work to matrix
   rows.
6. If a phase fails because the prompt is too large, split only at the suggested
   boundaries inside the phase prompt, preserving all tests and forbidden
   shortcuts.

## Phase Prompt Template

Use this shape for every implementation turn:

```text
Use docs/iteration-prompts/00-goal-mode.md as the long-term guardrail.
Apply docs/iteration-prompts/00-global-iteration-rules.md as mandatory rules
for this turn.

For this turn, implement ONLY:
docs/iteration-prompts/<phase-file>.md

Do not implement the next phase.
Do not commit until I explicitly ask.
Stop after the phase final response contract.
```

## Current Baseline

At the time this directory was created, the repository was at Matrix P1:

- rows 1-10 and 26 are implemented;
- P2+ files are not implemented;
- `make test` passes the P1 suite;
- `make eval` still points to the Phase >1 placeholder.

Do not treat this directory as proof of implementation. It is a prompt package
for future implementation runs.
