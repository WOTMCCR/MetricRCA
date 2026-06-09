# Final Compliance Matrix

Status vocabulary: `satisfied`, `partial`, `intentionally deferred`, `missing`.

| Row | Status | Proof |
| --- | --- | --- |
| 1 | satisfied | `tests/test_domain_models.py` |
| 2 | satisfied | `tests/test_settings.py` |
| 3 | satisfied | `tests/test_schema.py` |
| 4 | satisfied | `tests/test_guard.py`, `tests/test_renderer.py` |
| 5 | satisfied | `tests/test_seed.py`, `PATH=.venv/bin:$PATH make seed` |
| 6 | satisfied | `tests/test_query_spec.py`, `tests/test_domain_models.py` |
| 7 | satisfied | `tests/test_renderer.py` |
| 8 | satisfied | `tests/test_guard.py` |
| 9 | satisfied | `tests/test_guard.py`, A/E grep scans |
| 10 | satisfied | `tests/test_repository.py`, `tests/test_tools.py` |
| 11 | satisfied | `tests/test_anomaly.py`, `tests/test_tools.py` |
| 12 | satisfied | `tests/test_attribution.py`, `tests/test_tools.py` |
| 13 | satisfied | `tests/test_metadata_service.py` |
| 14 | satisfied | `tests/test_tools.py` |
| 15 | satisfied | `tests/test_tools.py` |
| 16 | satisfied | `tests/test_reflection.py`, `tests/test_reporting.py` |
| 17 | satisfied | `tests/test_trace.py`, `tests/test_graph.py` |
| 18 | satisfied | `tests/test_graph.py`, `tests/test_react.py` |
| 19 | satisfied | `tests/test_reflection.py`, `tests/test_graph.py` |
| 20 | satisfied | `tests/test_memory.py`, `tests/test_graph.py` |
| 21 | satisfied | `tests/test_api.py` |
| 22 | satisfied | `tests/test_ui_smoke.py`, `npm test --prefix frontend -- --run`, `npm run build --prefix frontend` |
| 23 | satisfied | `tests/test_eval.py`, `PATH=.venv/bin:$PATH make eval` |
| 24 | satisfied | `tests/test_zero_fallback.py`, A/E grep scans, GT leakage scan |
| 25 | satisfied | `tests/test_docs_compliance.py`, `README.md`, `docs/architecture.md`, `screenshots/README.md` |
| 26 | satisfied | `tests/test_project_contract.py` |
| 27 | satisfied | `PATH=.venv/bin:$PATH make test`, `python -W error::ResourceWarning -m unittest discover -s tests -v` |

## Remaining Deviations

- Bounded SQL retry is intentionally deferred. Current behavior is typed
  fail-fast `SQL_EXECUTION_FAILED`; it does not retry or silently continue.
  This is a non-P0 hardening follow-up.

No P0 rows are partial or missing.
