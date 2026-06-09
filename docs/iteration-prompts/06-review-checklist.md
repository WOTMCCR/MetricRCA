# Post-Implementation Review Checklist

Every iteration must pass this checklist **before** claiming completion.
Paste into the review prompt or append to the phase prompt's ACCEPTANCE CHECKS.

```text
POST-IMPLEMENTATION REVIEW CHECKLIST

This checklist is mandatory for every phase completion. Each item must be
verified with evidence (grep output, test name, or file:line reference).
A "yes by visual inspection" is not evidence — automated proof is required.

─────────────────────────────────────────────────────────────────────────
A. DATA SOURCE FIDELITY (prevents "hardcode pretending DB" class of bugs)
─────────────────────────────────────────────────────────────────────────

A1. Source scan: no metric-definition constants in services or tools
    Command:
      grep -rn "METRIC_DEFINITIONS\|MetricDefinition(" \
        metric_rca/services/ metric_rca/agent/ \
        --include="*.py" | grep -v "import\|type hint\|: MetricDefinition"
    Expected: zero hits (construction of MetricDefinition objects in
    service/tool runtime code means hardcoded metadata).

A2. Source scan: no schema-context constants in services or tools
    Command:
      grep -rn "SCHEMA_CONTEXT\|schema_context\s*=" \
        metric_rca/services/ metric_rca/agent/ \
        --include="*.py" | grep -v "import\|def \|param\|arg"
    Expected: zero hits.

A3. Source scan: no seeded dimension-value constants in services or tools
    Command:
      grep -rn "_CHANNELS\|_CATEGORIES\|paid_ads\|organic\|affiliate\|electronics\|fashion\|home" \
        metric_rca/services/ metric_rca/agent/ \
        --include="*.py" | grep -v "import\|test\|#\|docstring"
    Expected: zero hits of hardcoded dimension values. Dimension values
    must come from DB metadata or be passed as typed parameters.

A4. Proof test exists: mutate persisted metric metadata → runtime error changes
    Test name pattern: test_*mutate*metric*metadata* or test_*drop*metric*
    Behavior: delete or rename a metric_definition row in the metadata source,
    then call get_metric_definition → must raise METRIC_NOT_FOUND.
    If metadata is hardcoded, this test trivially passes (the mutation has no
    effect), so the test MUST operate on the actual data source (DB/repo).

A5. Proof test exists: source scan rejects service-level constants
    Test name: test_services_and_tools_have_no_hardcoded_metric_definitions
    Behavior: read all .py files under metric_rca/services/ and metric_rca/agent/,
    assert none contain "METRIC_DEFINITIONS" or MetricDefinition(...) construction.

─────────────────────────────────────────────────────────────────────────
B. COMPLIANCE MATRIX CROSS-CHECK
─────────────────────────────────────────────────────────────────────────

B1. For each targeted matrix row, verify:
    - "Required behavior" column: each clause has a passing test
    - "Proof tests" column: each named test exists and passes
    - "Shortcut-to-avoid" column: no code matches the described shortcut

B2. For each targeted matrix row's "Shortcut-to-avoid" column:
    Run an automated scan or proof test that would fail if the shortcut
    were present. "I read the code and it looks fine" is not evidence.

B3. Cross-reference the "访问 DB" column in §13 Tool Contracts table:
    For every tool/function marked "访问 DB=是":
      verify the implementation reads from a repository/DB, not from
      a hardcoded dict, module-level constant, or seed-derived value.
    For every tool/function marked "访问 DB=否":
      verify the implementation does NOT import or call any repository.

─────────────────────────────────────────────────────────────────────────
C. ARCHITECTURE BOUNDARY VERIFICATION
─────────────────────────────────────────────────────────────────────────

C1. Services purity: services/*.py must not import MetricRepository,
    SQLRenderer, SQLGuard, create_engine, pandas, pymysql.
    (Existing test: test_services_do_not_import_db_or_repository_modules)

C2. Tool pipeline: every tool must go through
    QuerySpec → SQLRenderer → SQLGuard → Repository.execute_plan.
    Verify with spy/mock assertions (existing tests).

C3. Evidence chain: every data tool emits Evidence with run-scoped
    globally unique evidence_id ("{run_id}:E{n}").
    Verify each tool test asserts evidence persistence.

C4. Guard non-bypass: guard_status != "passed" → no execute_plan call.
    Verify with test_tool_guard_rejection_returns_typed_error.

─────────────────────────────────────────────────────────────────────────
D. EXTENSIBILITY & INTERFACE CONTRACTS
─────────────────────────────────────────────────────────────────────────

D1. New business logic does not hardcode values that should come from:
    - DB tables (metric_definition, dim_product, etc.)
    - Configuration (settings.py)
    - Upstream typed parameters

D2. For functions marked "LLM 可调" or "由 LLM 辅助" in §13:
    verify the implementation has an extensibility seam (Protocol,
    strategy pattern, or dependency injection) that allows swapping
    in an LLM-backed implementation without changing callers.

D3. Typed error codes match §13 Tool Contracts table exactly.
    No invented error codes; no missing documented error codes.

─────────────────────────────────────────────────────────────────────────
E. FORBIDDEN PATTERN SCAN
─────────────────────────────────────────────────────────────────────────

E1. Runtime code does not reference anomaly_ground_truth.
    (Existing test: test_runtime_code_does_not_read_anomaly_ground_truth)

E2. No broad exception swallowing:
    grep -rn "except Exception" metric_rca/services/ metric_rca/agent/

E3. No direct SQL in tools/services:
    grep -rn "read_sql\|create_engine\|pymysql\|\.execute(" \
      metric_rca/services/ metric_rca/agent/

E4. No fabricated evidence (candidates without current-run evidence_ids).

─────────────────────────────────────────────────────────────────────────
F. REVIEW PROCESS
─────────────────────────────────────────────────────────────────────────

F1. Run every grep/scan command in sections A and E above.
    Paste the actual output (not "I checked and it's clean").

F2. List each compliance matrix row targeted by this phase.
    For each row, state: row number, the shortcut-to-avoid text,
    and the specific test or scan that proves the shortcut is absent.

F3. If any check fails, it is a blocking defect. Do not list it as
    "Known shortcut" — fix it before claiming completion.
```
