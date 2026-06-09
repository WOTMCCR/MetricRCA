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

5. Preserve real metadata contracts:
   get_metric_definition and get_schema_context must be backed by persisted
   metadata, schema metadata, or an explicit metadata repository. Do not
   hardcode metric_definition rows, schema context, seeded dimension values,
   channel/category lists, or product IDs in runtime services. Fixed MVP
   question families constrain parse intent only.

6. SQLGuard must remain sqlglot AST-based. Regex/string checks may only be
   supplementary.

7. Evidence is mandatory. No root cause, report numeric claim, confirmed/likely
   verdict, or operation task may exist without current-run guard-passed
   Evidence.

8. No silent fallback:
   no broad except Exception: continue
   no empty-data continuation
   no default provider substitution
   no LLM-only bypass
   no SQLGuard bypass
   no Memory-derived conclusion
   no report after failed Reflection.

9. Node/tool files must be real modules with real behavior. No empty
   placeholders, no re-export shells, no giant graph.py sequential orchestrator.

10. Reflection is a deterministic rule verifier. Repair must re-enter legal
   ReAct/tool/query path and produce new Evidence before passing.

11. Memory only affects drilldown priority. Low-confidence, expired, or
    lower-version records must be ignored. Required memory failure is typed
    failure.

12. API/UI must be real surfaces over persisted graph outputs. No CLI print
    pretending API, no print(json) pretending Streamlit.

13. Eval must read and validate anomaly_ground_truth. dangerous_sql_blocked must
    be a real boolean derived from actual SQLGuard negative behavior.

14. Do not use anomaly_ground_truth in runtime services, tools, graph,
    reflection, memory, API report generation, or attribution. It is allowed
    only in seed, eval, and tests.

15. Every final response must include:
    Files changed
    Tests added/updated
    Commands run
    Test output summary
    Docs requirements satisfied by matrix row
    Remaining deviations by matrix row
    Fallback-like code touched and why it is fail-fast
    Known shortcuts: []

17. Before claiming phase completion, run the full post-implementation
    review checklist in docs/iteration-prompts/06-review-checklist.md.
    Paste actual grep/scan output for every item in sections A and E.
    A failing checklist item is a blocking defect.

16. The command `python -W error::ResourceWarning -m unittest discover -s tests
    -v` is a ResourceWarning/import smoke unless additional unittest tests are
    intentionally added. Full functional verification is `make test` plus the
    phase-specific pytest commands.
```
