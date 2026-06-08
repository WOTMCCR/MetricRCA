# Project Instructions

## Project Context

- `docs/MetricRCA.md` is the implementation source of truth.
- `docs/deep-research-report.md` is research/background/interview material.
- `docs/MetricRCA-roadmap-checklist.md` is the executable roadmap and checklist.
- Do not implement arbitrary Text-to-SQL, MCP, Multi-Agent, Vector DB, auth, multi-tenant, or dashboard-heavy UI for the MVP unless explicitly requested.
- Use deterministic `QuerySpec -> SQLRenderer -> SQLGuard -> Repository` as the only data access path.
- Avoid fallback-like behavior: no LLM-only bypass, no broad exception swallowing, no silent degradation, no default provider substitution, and no empty-data continuation.

## Environment

- Last preflight: 2026-06-08T11:32:02+08:00; see `docs/env-setup.md`.
- Runtime available: Python 3.12.3, Node v20.19.6.
- Network: GitHub, npm registry, and PyPI reachable through current environment.
- Proxy: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and lowercase variants are set to localhost proxy ports; `NO_PROXY` includes localhost loopback addresses.
- Local service traffic must avoid proxy leakage; for Python `httpx` local calls use `trust_env=False`.
- Project is in WSL on a native Linux path.
- No package dependencies are installed yet because no app stack marker exists.
