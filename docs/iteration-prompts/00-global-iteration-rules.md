# Global MetricRCA Iteration Rules

Paste this block at the top of every phase prompt.

```text
GLOBAL METRICRCA ITERATION RULES

1. Source of truth priority:
   current user instruction > AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md >
   docs/MetricRCA.md > docs/MetricRCA-roadmap-checklist.md >
   docs/COMPLIANCE_MATRIX.md > background docs.

2. Implement by compliance matrix rows. Before writing code, state which rows
   this phase targets and which rows remain deferred.

3. Tests first. Add proof tests that fail against shortcuts before implementing
   production code. Do not weaken existing tests.

4. Preserve the sole metric-fact data path:
   QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan.
   No raw SQL execution, no pandas.read_sql, no direct SQLAlchemy connections in
   services/tools/agent nodes.

5. SQLGuard must remain sqlglot AST-based. Regex/string checks may only be
   supplementary.

6. Evidence is mandatory. No root cause, report numeric claim, confirmed/likely
   verdict, or operation task may exist without current-run guard-passed
   Evidence.

7. No silent fallback:
   no broad except Exception: continue
   no empty-data continuation
   no default provider substitution
   no LLM-only bypass
   no SQLGuard bypass
   no Memory-derived conclusion
   no report after failed Reflection.

8. Node/tool files must be real modules with real behavior. No empty
   placeholders, no re-export shells, no giant graph.py sequential orchestrator.

9. Reflection is a deterministic rule verifier. Repair must re-enter legal
   ReAct/tool/query path and produce new Evidence before passing.

10. Memory only affects drilldown priority. Low-confidence, expired, or
    lower-version records must be ignored. Required memory failure is typed
    failure.

11. API/UI must be real surfaces over persisted graph outputs. No CLI print
    pretending API, no print(json) pretending Streamlit.

12. Eval must read and validate anomaly_ground_truth. dangerous_sql_blocked must
    be a real boolean derived from actual SQLGuard negative behavior.

13. Do not use anomaly_ground_truth in runtime services, tools, graph,
    reflection, memory, API report generation, or attribution. It is allowed
    only in seed, eval, and tests.

14. Every final response must include:
    Files changed
    Tests added/updated
    Commands run
    Test output summary
    Docs requirements satisfied by matrix row
    Remaining deviations by matrix row
    Fallback-like code touched and why it is fail-fast
    Known shortcuts: []

15. The command `python -W error::ResourceWarning -m unittest discover -s tests
    -v` is a ResourceWarning/import smoke unless additional unittest tests are
    intentionally added. Full functional verification is `make test` plus the
    phase-specific pytest commands.
```
