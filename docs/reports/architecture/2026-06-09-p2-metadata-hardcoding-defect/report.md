# Matrix P2 Metadata Hardcoding Defect Report

Date: 2026-06-09
Project: MetricRCA
Function area: architecture / docs compliance
Status: Open defect

## Executive Summary

Matrix P2 introduced a docs-compliance defect in
`metric_rca/services/metric_service.py`: runtime metric metadata and schema
context were hardcoded in the service layer.

This is not required by the design documents. The docs only support a fixed MVP
question family and typed parse errors. They do not authorize hardcoding
`metric_definition`, schema context, seeded dimension values, channel/category
values, or product IDs in runtime services.

The implementation should be treated as an open deviation until remediated by a
DB-backed metadata path with shortcut-resistant tests.

## Defect

### What Was Implemented

`metric_service.py` currently contains:

- `METRIC_DEFINITIONS`: hardcoded runtime metric definitions.
- `SCHEMA_CONTEXT`: schema context derived from that hardcoded dict.
- `_CHANNELS`, `_CATEGORIES`, and product IDs: seeded data values embedded in
  parse logic.

### Why This Is Wrong

The design documents require:

- Fixed question families for MVP, not arbitrary Text-to-SQL.
- Explicit typed errors for unsupported metric, dimension, date, or parse
  failures.
- `get_metric_definition` returns `MetricDefinition` and has DB access.
- `get_schema_context` returns schema context and has DB access.

They do not require or permit duplicating runtime metadata from
`metric_definition` or schema tables into a service-level constant.

## Evidence

Design references:

- `docs/MetricRCA.md` §1.1: fixed six MVP question families.
- `docs/MetricRCA.md` §1.4: typed errors such as `METRIC_NOT_FOUND`,
  `DIMENSION_NOT_ALLOWED`, `DATE_RANGE_INVALID`, and `PARSE_FAILED`.
- `docs/MetricRCA.md` §13 Tool Contracts:
  - `parse_question`: no DB access.
  - `get_metric_definition`: DB access, returns `MetricDefinition`.
  - `get_schema_context`: DB access, returns schema dict.
- `docs/COMPLIANCE_MATRIX.md` row 12: tool layer includes
  `services/metric_service.py` but must preserve real behavior.
- `docs/COMPLIANCE_MATRIX.md` row 27: extended tool contracts require typed
  contracts for `get_metric_definition` and `get_schema_context`.

Code evidence:

- `metric_rca/services/metric_service.py` hardcodes metric definitions.
- `metric_rca/services/metric_service.py` hardcodes schema context derivation.
- `metric_rca/services/metric_service.py` hardcodes seeded channel/category and
  product values in parsing.

## Impact

- Runtime metadata can drift from seeded `metric_definition` rows.
- Tests can pass against duplicated constants while the real database metadata
  path is broken.
- `get_metric_definition` and `get_schema_context` do not prove the documented
  DB-backed contracts.
- The implementation weakens the repository-level rule that tests must encode
  docs requirements rather than shortcuts.

## Root Cause

During P2 implementation, the phrase "parse only the six MVP question families"
was over-applied. It correctly justifies a controlled parser and fixed question
families, but it does not justify hardcoding metric metadata, schema context, or
seeded dimension values inside runtime services.

The proof tests focused on typed errors and tool behavior, but did not assert
that metadata contracts read persisted metadata instead of service constants.

## Remediation Requirements

Future remediation must:

1. Move `get_metric_definition` to a DB-backed metadata path, likely through a
   repository method or dedicated metadata repository.
2. Move `get_schema_context` to a DB-backed schema/metadata path.
3. Keep `parse_question` deterministic and controlled, but limit it to intent
   parsing; it must not embed seeded dimension values as truth.
4. Add tests that fail if `metric_definition` DB rows change but runtime returns
   stale hardcoded values.
5. Add tests that mutate or spy on metadata access and prove
   `METRIC_NOT_FOUND` / `SCHEMA_CONTEXT_MISSING` are produced by the real
   metadata source.
6. Remove or replace hardcoded channel/category/product value lists unless they
   are derived from a documented metadata source.

## Strengthened Hard Requirements

These requirements are now mandatory for future iterations:

- Runtime metric metadata must not be duplicated as service-level constants.
- `get_metric_definition` and `get_schema_context` must be backed by persisted
  metadata or schema introspection, not hardcoded dictionaries.
- Fixed MVP question families do not authorize hardcoded metric definitions,
  schema context, or seeded dimension values.
- If a phase temporarily leaves metadata hardcoded, the final response must list
  it as a remaining deviation and `Known shortcuts` must not be `[]`.
- Review prompts for subagent and Claude CLI review must include a metadata
  hardcoding check when the touched scope includes parsing, tools, repositories,
  schema, or services.

## Required Follow-Up Tests

Add or update tests so a shortcut implementation fails:

- Changing `metric_definition.higher_is_better` in the DB changes
  `get_metric_definition` output.
- Missing metric row returns `METRIC_NOT_FOUND`.
- Missing schema context returns `SCHEMA_CONTEXT_MISSING`.
- `metric_service.py` does not contain runtime metric-definition dictionaries
  or seeded dimension-value constants.
- Tool execution obtains metric metadata through the DB-backed metadata path.

## Current Status

Open. The code has not been remediated in this report-writing turn. This report
documents the defect and hardens future iteration requirements.

