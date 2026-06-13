# Fix 002 - P6 deepagents: close the acceptance gaps (no defective ship)

> Context: P6 (commits d422d00 docs + 69ebe99 code on branch
> `codex/p6-deepagents-core`) migrated `metric_rca/agent/` to deepagents and is
> unit-green (212 passed), but an adversarial review found TWO blocking defects
> and three medium defects. The structural migration is sound; do NOT redo it.
> This fix closes the acceptance gaps. Work on the SAME branch
> `codex/p6-deepagents-core` and keep all currently-passing tests green.

```text
You are fixing P6 on branch codex/p6-deepagents-core in the MetricRCA repo.

MANDATORY PRELUDE — read and obey before touching anything:
1. docs/iteration-prompts/00-global-iteration-rules.md
2. docs/iteration-prompts/06-review-checklist.md
3. docs/final-design/01-architecture.md (esp. §3 GuardMiddleware, §4 builtin
   tool governance) and 03-flows.md (§4 guard/budget flow)
4. docs/reference/decisions.md ADL-0008 (its 后续跟进 already documents the
   deepagents filesystem-tool uncertainty — you must now RESOLVE it, not
   restate it)

ENVIRONMENT (capabilities you now have — the "not installed" excuse is gone):
- Use uv for env + deps. Network is good; install ALL pinned deps including
  deepagents==0.3.5 and the langchain/langgraph pins from pyproject.toml.
  Commands (run from repo root, adapt if the project standardizes on uv):
    uv venv .venv
    uv pip install -e .
  Then run project commands with PATH=.venv/bin:$PATH so the Makefile resolves
  python to the venv. Confirm deepagents imports:
    PATH=.venv/bin:$PATH python -c "import deepagents, langgraph, langchain; \
      from deepagents import create_deep_agent; print('deepagents OK')"
  If any pinned version does not exist / conflicts, STOP and report the exact
  resolver error. Do not silently bump a version to make install succeed; if a
  pin must change, justify it and update ADL-0008's pinned-version list in the
  same commit.

──────────────────────────────────────────────────────────────────────
BLOCKING DEFECT B2 — filesystem-tool governance is wrong for deepagents 0.3.5
──────────────────────────────────────────────────────────────────────
Verified fact (context7 /langchain-ai/deepagents source + ADL-0008 note):
create_deep_agent governs built-in filesystem tools (ls, read_file, write_file,
edit_file, glob, grep) via `permissions` (FilesystemPermission with
allow/deny/interrupt), NOT via a `builtin_tools` parameter. The current factory
passes `builtin_tools=[]` and `permissions=[]`; the `_supports_kwarg(...,
"builtin_tools")` guard returns True whenever create_deep_agent has **kwargs, so
the DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE fail-fast does NOT fire in the real
case, and `permissions=[]` adds no deny rule. Net result: either every run dies
as a mislabeled LLM error, or filesystem tools leak into the action space.

Required fix:
1. Disable built-in filesystem tools the correct way for the PINNED version.
   Verify the exact mechanism against the installed deepagents==0.3.5 source/API
   (do not guess). Expected approach: deny-all FilesystemPermission, e.g.
   permissions=[FilesystemPermission(operations=["read","write"], paths=["/**"],
   mode="deny")] — confirm the precise import path and that deny actually
   prevents the tools from being callable. If deepagents 0.3.5 genuinely cannot
   remove/deny these tools, that is a fail-closed typed error
   (DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE) AND a STOP-and-report — do not
   ship an agent that exposes ls/read_file/write_file/edit_file/glob/grep.
2. Replace the self-referential differential test. The new test MUST construct a
   REAL agent via the installed deepagents (no mock factory) and INTROSPECT THE
   COMPILED AGENT's actual exposed tool set (find the correct introspection path
   for 0.3.5 — e.g. the ToolNode / bound tools on the compiled graph), then
   assert:
     - the exposed set CONTAINS the whitelist
       {detect_anomaly, drilldown_dimension, fetch_related_signal,
        calculate_contribution, rank_root_causes} plus write_todos, AND
     - the exposed set CONTAINS NONE of
       {ls, read_file, write_file, edit_file, glob, grep}.
   This test builds the agent only (no agent.invoke), so it needs NO LLM API key
   and MUST run in CI/offline. Mark it to skip ONLY if deepagents import fails,
   never to fake-pass. Constructing bundle.exposed_tool_names from our own tools
   list is NOT acceptable evidence — introspect the framework object.

──────────────────────────────────────────────────────────────────────
BLOCKING DEFECT B1 — the eval acceptance gate was never met (eval is red)
──────────────────────────────────────────────────────────────────────
The latest eval before the P6 commit showed thresholds_met=false, top1=0.2,
anomaly_accuracy=0, sql_safe_rate=0 — because the agent could not be built. After
B2 is fixed and deepagents is installed, the 5-case eval must pass for real.

Required:
- The eval path (metric_rca/evals/runner.py -> run_rca -> create_metric_rca_agent
  -> real deepagents + real LLM) must NOT be mocked. The only place a fake model
  is allowed is inside unit-test fixtures at the agent_factory boundary; the eval
  runner and production code paths must require a real configured model.
- Real eval requires MySQL (make up) and real LLM credentials
  (METRIC_RCA_LLM_PROVIDER, METRIC_RCA_LLM_MODEL, METRIC_RCA_LLM_API_KEY, or
  OPENAI_API_KEY). If you HAVE working LLM credentials in this environment:
  run the full acceptance below and paste real output. If you DO NOT have LLM
  credentials, you MUST:
    (a) complete B2 + the deterministic tests + the medium fixes,
    (b) make the eval path correct and runnable, and
    (c) STOP at the eval step and emit a clearly-labeled
        "HUMAN-GATED ACCEPTANCE REQUIRED" block listing the exact commands the
        supervisor must run, and explicitly state that you did NOT observe a
        green eval. Do NOT claim eval-green you did not run. Do NOT lower any
        threshold. Do NOT edit eval cases or scorer to manufacture green.

──────────────────────────────────────────────────────────────────────
MEDIUM DEFECTS — fix all three with paired proof tests
──────────────────────────────────────────────────────────────────────
B3 (error masking): runner.py agent.invoke handler defaults unknown exceptions
   to LLM_REQUIRED_UNAVAILABLE and ignores exc.code. Preserve getattr(exc,'code')
   first (like the factory-construction handler already does), and use a
   non-LLM default (e.g. AGENT_INVOKE_FAILED) when the real code is unknown, so a
   tool/DB/trace failure during invoke is never mislabeled "LLM unavailable".
   Test: a tool that raises a coded error during invoke surfaces that exact code.
B4 (one bad arg kills the run): per docs/final-design/03-flows.md §4, a single
   ACTION_SCHEMA_INVALID must be RECOVERABLE — the LLM gets the typed error and
   may retry with valid args; only the SECOND consecutive illegal call to the
   SAME tool terminates the run. Implement that semantics in GuardMiddleware /
   RunGuardContext (track consecutive-illegal per tool; do not mark_failed on the
   first). Tests: (1) one illegal-args call then a valid call -> run continues;
   (2) two consecutive illegal calls to the same tool -> run failed.
B5 (budget blocks the finalizer): GuardMiddleware._budget_error returns the
   step-budget rejection for ALL tools once step_count>=max_steps, including
   rank_root_causes, yet the message tells the LLM to "call rank_root_causes or
   stop". Exempt rank_root_causes and write_todos from the step-budget block
   (data-fetching budget still applies to data tools) so the agent can always
   finalize. Test: at step budget, rank_root_causes is allowed once while
   data-fetching tools are rejected with BUDGET_EXCEEDED.

──────────────────────────────────────────────────────────────────────
DO-NOT-REGRESS INVARIANTS (re-verify, do not weaken)
──────────────────────────────────────────────────────────────────────
- QuerySpec->SQLRenderer->SQLGuard->Repository is the only metric-fact path.
- MetadataRepository->MetricService is the only metadata path; no hardcoded
  metric/dimension/family lists anywhere in agent code.
- ADL-0006 report projection: numeric values only in numeric_claims bound to
  persisted Evidence.
- Zero-fallback negative tests stay present and meaningful.
- docs-first: if any behavior contract changes (B4/B5 semantics), update
  docs/final-design + docs/COMPLIANCE_MATRIX in a docs commit BEFORE the code
  commit, and name the proof test for each changed row.

──────────────────────────────────────────────────────────────────────
ACCEPTANCE — evidence-before-done. Paste ACTUAL command output for each.
──────────────────────────────────────────────────────────────────────
1. uv install proof: the `import deepagents ... print('deepagents OK')` line.
2. PATH=.venv/bin:$PATH python -m pytest -q   -> all green, count >= current 212,
   and the new B2 real-introspection test + B3/B4/B5 tests included and passing.
3. The B2 introspection test output proving filesystem tools are absent from the
   REAL compiled agent.
4. Full eval (if creds available):
     PATH=.venv/bin:$PATH make up && PATH=.venv/bin:$PATH make seed && \
     PATH=.venv/bin:$PATH make eval
   Paste the eval summary JSON: case_total=5, anomaly_accuracy=1.0,
   sql_safe_rate=1.0, top1_rate>=0.8 (5-case set), no_anomaly_correct=true,
   thresholds_met=true. If creds NOT available -> the HUMAN-GATED block from B1.
5. Re-run the 06-review-checklist section A + E scans and paste output (zero
   hits expected).

FORBIDDEN SHORTCUTS (any of these = defective ship = reject):
- Faking, mocking, or stubbing the LLM anywhere in the eval or production path.
- Asserting tool exposure from our own constructed list instead of introspecting
  the real compiled agent.
- Lowering thresholds, editing eval cases/scorer, or marking tests xfail/skip to
  manufacture green.
- Claiming an eval result you did not actually run.
- Leaving filesystem tools exposed "because deny was hard".
- Bumping a pinned dependency silently to force install.

DELIVERABLE: ordered commit list (docs/matrix commits first if any contract
changed, then code), each with message + file list, plus the pasted acceptance
output above. End with an honest status: either "ALL ACCEPTANCE GREEN (incl.
eval)" or "CODE COMPLETE — EVAL HUMAN-GATED (not observed green by me)".
```
