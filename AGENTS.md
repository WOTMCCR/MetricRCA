# Project Instructions

This repository intentionally restarts from the docs-only implementation plan at
base commit `b6b4b5bcf694cbd20ff25cde8aedae8ae253571b`. Do not treat the absence
of application code as permission to implement a smaller prototype.

Read and follow `docs/IMPLEMENTATION_CONTRACT.md` before any implementation
task. That contract is a repository-level gate for future Codex work.

`docs/COMPLIANCE_MATRIX.md` is the binding, row-by-row implementation gate
derived from the docs (27 rows: required files, required behavior, proof tests,
phase, shortcut-to-avoid). It was produced per the contract's Required Work
Process and adversarially reviewed by Codex. Future work must satisfy the
relevant rows and keep their proof tests passing; update the matrix when a phase
lands or scope changes — never weaken a proof test to fit a shortcut.

Do not optimize for green tests by simplifying architecture. First make tests
encode the docs, then make the implementation pass those tests.

## Project Context

- `docs/MetricRCA.md` is the engineering design source of truth.
- `docs/MetricRCA-roadmap-checklist.md` is the executable Definition of Done.
- `docs/deep-research-report.md` is background/interview material only.
- Do not implement arbitrary Text-to-SQL, MCP, Multi-Agent, Vector DB, auth, multi-tenant, or dashboard-heavy UI for the MVP unless explicitly requested.
- Use deterministic `QuerySpec -> SQLRenderer -> SQLGuard -> Repository` as the only data access path.
- Avoid fallback-like behavior: no LLM-only bypass, no broad exception swallowing, no silent degradation, no default provider substitution, and no empty-data continuation.

## Environment

- Last preflight refresh: 2026-06-09T08:43:00+08:00; see `docs/env-setup.md`.
- Runtime available: Python 3.12.3, Node v20.19.6.
- Network: GitHub, npm registry, and PyPI reachable through current environment.
- Proxy: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and lowercase variants are set to localhost proxy ports; `NO_PROXY` includes localhost loopback addresses.
- Local service traffic must avoid proxy leakage; for Python `httpx` local calls use `trust_env=False`.
- Project is in WSL on a native Linux path.
- Python dependencies are installed in project-local `.venv`; use `PATH=.venv/bin:$PATH make seed` and `PATH=.venv/bin:$PATH make test` so Makefile `python` resolves correctly.

## Strict Implementation Contract

Passing the 5 MVP eval cases is necessary but not sufficient. The task is docs
compliance, not "minimal code that makes 5 cases pass." Do not redefine MVP as a
small deterministic prototype.

A change is acceptable only if:
1. It satisfies the relevant docs/MetricRCA.md and docs/MetricRCA-roadmap-checklist.md requirements.
2. It includes tests that would fail against a shortcut implementation.
3. It does not replace required architecture with a simpler placeholder.
4. It does not leave files as empty placeholders or re-export-only modules when docs assign them responsibilities.
5. It does not redefine MVP scope without updating docs and explicitly documenting the deviation.

The following are explicitly unacceptable:
- plain sequential function pretending to be LangGraph
- empty placeholder node/tool modules
- CLI print pretending to be FastAPI
- print(json) pretending to be Streamlit
- runtime service constants pretending to be DB-backed metric metadata or schema context
- regex SQLGuard pretending to be sqlglot AST guard
- hardcoded eval success
- dangerous_sql_blocked = null
- broad `except Exception: continue`
- empty-data attribution
- SQLGuard bypass
- report generation after failed Reflection
- root cause without current-run Evidence
- Memory-derived conclusion without current-run Evidence

## Required Architecture Gate

Future implementation work must preserve these architecture requirements:

- Real LangGraph `StateGraph`, with the documented nodes and conditional flow.
- Real ReAct `AgentAction -> Observation -> Evidence` loop.
- Real deterministic tool layer in the documented tool modules.
- `QuerySpec -> SQLRenderer -> SQLGuard -> Repository` as the only data access path.
- Metric metadata and schema context must be DB-backed or schema-backed through
  repository/metadata contracts. Do not duplicate `metric_definition`, schema
  context, seeded dimension values, channel/category lists, or product IDs as
  runtime service constants.
- `SQLGuard` implemented with sqlglot AST, not regex or prompt checks.
- Reflection implemented as a rule verifier with the documented repair path.
- Memory constrained to planning influence; it cannot become a final conclusion.
- FastAPI and Streamlit implemented as real app surfaces, not CLI placeholders.
- Eval reads `anomaly_ground_truth` and computes real `dangerous_sql_blocked`.

## Required Work Process

Before writing application code:

1. Read `docs/MetricRCA.md`, `docs/MetricRCA-roadmap-checklist.md`,
   `docs/deep-research-report.md`, `docs/env-setup.md`, this file, and
   `docs/IMPLEMENTATION_CONTRACT.md`.
2. The docs compliance matrix is already produced and persisted at
   `docs/COMPLIANCE_MATRIX.md` (requirement, docs reference, required files,
   required behavior, proof tests, phase, shortcut-to-avoid). Read it first.
3. Do not start implementation until you have mapped your task to specific
   matrix rows and their proof tests.

During implementation:

- For each phase, write failing tests first or update tests so they encode the
  docs requirements.
- Do not weaken tests to fit shortcuts.
- Do not implement source files as empty placeholders or re-export-only modules
  when docs assign responsibilities.
- Do not satisfy `get_metric_definition` or `get_schema_context` with hardcoded
  dictionaries. Tests must fail if persisted metadata changes but runtime
  returns stale constants.
- Every final response must include commands run, test output summary, and
  remaining deviations.

Before every future iteration final response:

- Run a subagent code review after local verification.
- Run Claude CLI review with `--model opus --effort high` when local CLI
  authentication is available; if authentication, model access, or CLI
  availability blocks it, report the exact failure and do not silently
  substitute another reviewer.
