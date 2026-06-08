# MetricRCA Phase 1 Data And Guardrails Implementation Plan

**Goal:** Implement only Phase 1 rows 1-10 and 26 against the docs-compliance gate.
**Acceptance criteria:** MySQL 8 compose stack, exact §9 DDL, deterministic seed with 5 ground-truth cases, Pydantic v2 contracts, QuerySpec to renderer to sqlglot guard to repository path, read-only execution, sql_audit writes, and required Phase 1 tests passing.
**Primary files/systems:** `pyproject.toml`, `docker-compose.yml`, `Makefile`, `metric_rca/config`, `metric_rca/data`, `metric_rca/domain`, `metric_rca/guardrails`, `metric_rca/repositories`, `tests`.
**Validation:** `make up`, `make seed`, `make test`, `python -W error::ResourceWarning -m unittest discover -s tests -v`.

## Task 1: Encode Proof Tests

**Addresses:** Matrix rows 1-10 and 26.
**Files:** `tests/test_schema.py`, `tests/test_seed.py`, `tests/test_query_spec.py`, `tests/test_renderer.py`, `tests/test_guard.py`, `tests/test_repository.py`.
**Work:** Add tests that fail for missing tables, shortcut seed data, raw SQL execution, regex-only guard behavior, invalid QuerySpec inputs, renderer/guard mismatch, and missing audit rows.
**Validation:** Run `python -m pytest -q` after dependency install and confirm red before production implementation.
**Stop/ask if:** Docker or MySQL is unavailable, because SQLite or in-memory substitution is forbidden.

## Task 2: Build Phase 1 Package And MySQL Stack

**Addresses:** Matrix rows 1-5 and 26.
**Files:** `pyproject.toml`, `docker-compose.yml`, `Makefile`, `metric_rca/config/settings.py`, `metric_rca/data/schema.sql`, `metric_rca/data/seed_data.py`, `metric_rca/data/anomaly_injection.py`.
**Work:** Define only Phase 1 dependencies, MySQL 8 service with schema init and read-only user, exact table DDL, typed settings with required DSNs, and deterministic idempotent seed generation.
**Validation:** `make up`, `make seed`, schema and seed tests.

## Task 3: Implement Contracts, Guardrails, And Repository

**Addresses:** Matrix rows 6-10.
**Files:** `metric_rca/domain/enums.py`, `metric_rca/domain/models.py`, `metric_rca/guardrails/query_spec.py`, `metric_rca/guardrails/renderer.py`, `metric_rca/guardrails/sql_guard.py`, `metric_rca/repositories/*.py`.
**Work:** Implement Pydantic models with `extra="forbid"`, whitelist QuerySpec builder, deterministic SQL rendering, sqlglot AST guard, and repository execution through passed `SQLPlan` only with sql_audit writes.
**Validation:** QuerySpec, renderer, guard, repository tests, then full Phase 1 command set.
