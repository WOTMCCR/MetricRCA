# Codex Goal Mode Prompt

Use this as the long-running Goal Mode objective. It is a guardrail for the
whole effort, not permission to implement all phases in one turn.

```text
You are implementing MetricRCA after Phase 1 data + guardrails are complete.

Overall goal:
Complete the remaining docs-compliant MetricRCA MVP by implementing Matrix P2,
P3, P4, and P5 in order, without shortcuts. Passing the five MVP eval cases is
necessary but not sufficient; architecture, evidence, reflection, memory,
API/UI, eval, and zero-fallback proof tests must also pass.

Source of truth priority:
1. Current user instruction
2. AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md
3. docs/MetricRCA.md
4. docs/MetricRCA-roadmap-checklist.md
5. docs/COMPLIANCE_MATRIX.md

Hard architecture requirements:
- QuerySpec -> SQLRenderer -> SQLGuard -> Repository is the only metric-fact
  data path.
- SQLGuard must remain sqlglot AST-based.
- Tools must be real files with typed args and must emit Observation +
  current-run Evidence.
- LangGraph must be a real StateGraph(RCAState) using START, END, add_node,
  add_edge, add_conditional_edges.
- run_rca must invoke the compiled graph.
- ReAct must be a real AgentAction -> Observation -> Evidence loop.
- Node files must be real modules, not re-export shells.
- Reflection must be deterministic rule verification; failed Reflection must not
  generate report.
- Repair must re-enter legal ReAct/tool/query path.
- Memory may only influence drilldown priority and never final conclusions.
- FastAPI and Streamlit must be real surfaces.
- Eval must read anomaly_ground_truth and compute dangerous_sql_blocked as a
  real boolean.

Execution rule:
Do not implement all phases at once. Stop after the requested phase. Each phase
must have tests first, required files, commands run, and Known shortcuts exactly
[].
For every phase turn, apply docs/iteration-prompts/00-global-iteration-rules.md
as mandatory rules before the phase-specific prompt.

Phase order:
1. Matrix P2: services + tools + evidence emission.
2. Matrix P3 Part A: real LangGraph + ReAct + trace + core reflection gate.
3. Matrix P3 continuation: full Reflection + Memory + zero-fallback hardening.
4. Matrix P4: FastAPI + Streamlit.
5. Matrix P5: Eval + README + architecture + final compliance.

Final response after each phase must include:
1. Files changed
2. Tests added/updated
3. Commands run
4. Test output summary
5. Docs requirements satisfied, mapped to matrix rows
6. Remaining deviations, mapped to matrix rows
7. Fallback-like code touched and why it is still fail-fast
8. Known shortcuts: []

If Known shortcuts is not exactly [], do not claim completion.
```
