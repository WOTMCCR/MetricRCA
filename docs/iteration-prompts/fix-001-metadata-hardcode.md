# Fix 001 — Eliminate Hardcoded Metric Metadata from Runtime Code

```text
You are working in the MetricRCA repository.

MANDATORY PRELUDE
Before applying this fix prompt, read and obey:
docs/iteration-prompts/00-global-iteration-rules.md (especially Rule 5)
docs/iteration-prompts/06-review-checklist.md (mandatory post-fix review)

GLOBAL RULES FOR THIS FIX
- Source of truth priority:
  1. Current user instruction
  2. AGENTS.md and docs/IMPLEMENTATION_CONTRACT.md
  3. docs/MetricRCA.md §13 Tool Contracts table (line 914-916)
  4. docs/COMPLIANCE_MATRIX.md rows 12 and 27
- Do not optimize for green tests by simplifying architecture.
- Preserve QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan.
- All existing P1+P2 tests must remain green after this fix.

DEFECT DESCRIPTION
metric_service.py hardcodes METRIC_DEFINITIONS (line 35), SCHEMA_CONTEXT
(line 108), _CHANNELS/_CATEGORIES (line 117-118), and product ID lists
(line 227-228) as module-level constants. This violates:

1. docs/MetricRCA.md §13 (line 915-916):
   get_metric_definition — 访问 DB=是
   get_schema_context — 访问 DB=是

2. docs/COMPLIANCE_MATRIX.md Row 12:
   "get_metric_definition / get_schema_context must not be hardcoded
   runtime dictionaries; metadata must come from persisted metadata,
   schema metadata, or explicit metadata repository."

3. docs/COMPLIANCE_MATRIX.md Row 27:
   "Metadata contracts are DB/schema-backed; fixed question families
   do not authorize hardcoded metric definitions, seeded dimension
   values, or schema context."

Additionally: seed_data.py only inserts 4 metrics (gmv, net_gmv, pay_cvr,
refund_rate) into metric_definition table, but metric_service.py hardcodes
8 metrics. The DB and the service are inconsistent.

ROOT CAUSE
The original implementation conflated "fixed 6 MVP question families"
(which is a parse_question concern) with "fixed metric metadata" (which
must come from DB per the design doc).

SECONDARY DEFECT (P0)
parse_question is implemented as a pure keyword if/elif parser with zero
generalization ability. §13 line 914 marks parse_question as "由 LLM 辅助"
and §0 says "LLM 仅负责：问题解析辅助". Even though the roadmap says
"MVP 可规则解析", the keyword approach is a dead end — it cannot handle
paraphrases, typos, mixed-language input, or any question outside the
exact keyword list. MVP MUST use an LLM planner for intent recognition.
The rule-based parser must be fully replaced, not kept as fallback.

TERTIARY DEFECT
fetch_related_signal.py (line 17) hardcodes _SIGNAL_METRIC mapping.
attribution_service.py (line 156) hardcodes _root_cause_type mapping.
These should be driven by metadata or configuration, not if/elif chains.

─────────────────────────────────────────────────────────────────────────
ARCHITECTURE DESIGN (read before implementing)
─────────────────────────────────────────────────────────────────────────

1. MetadataRepository (NEW: metric_rca/repositories/metadata_repository.py)

   class MetadataRepository:
       """Reads metric_definition and schema metadata from DB.
       All metadata access in services/tools goes through this."""

       def __init__(self, engine: Engine) -> None: ...

       def get_metric_definition(self, metric_id: str) -> MetricDefinition:
           """Read from metric_definition table. Raise METRIC_NOT_FOUND."""

       def get_schema_context(self, metric_id: str) -> dict[str, object]:
           """Derive schema context from metric_definition row.
           Raise SCHEMA_CONTEXT_MISSING."""

       def list_metrics(self) -> list[MetricDefinition]:
           """List all metric definitions (for intent parser context)."""

       def list_dimension_values(self, dimension: str) -> list[str]:
           """Read distinct dimension values from fact tables for the
           given dimension column. Used by intent parser."""

   Design constraints:
   - Uses the audit_engine (same as MetricRepository system table access).
   - Does NOT go through SQLRenderer/SQLGuard — it reads system/metadata
     tables, not business fact tables. This is consistent with the design
     doc: §13 marks get_metric_definition as "需守卫=否".
   - Returns domain model types (MetricDefinition), not raw dicts.
   - Raises typed MetricServiceError on not-found.

2. MetricService refactor (metric_rca/services/metric_service.py)

   Remove: METRIC_DEFINITIONS, SCHEMA_CONTEXT, _CHANNELS, _CATEGORIES,
   and all module-level metadata constants.

   Keep: MetricServiceError, ParsedIntent (typed models).
   Keep: parse_question as a function, but refactor to accept metadata
   context as parameters rather than reading from module globals.

   class MetricService:
       """Stateful service that holds a MetadataRepository reference."""

       def __init__(self, metadata_repo: MetadataRepository) -> None: ...

       def get_metric_definition(self, metric_id: str) -> MetricDefinition:
           """Delegates to metadata_repo. Typed error on not-found."""

       def get_schema_context(self, metric_id: str) -> dict[str, object]:
           """Delegates to metadata_repo. Typed error on missing."""

       def parse_question(
           self, question: str, *, business_today: date
       ) -> ParsedIntent:
           """MVP: rule-based parsing. Does NOT access DB (per §13).
           Uses self._intent_parser internally.
           Supported metrics/dimensions are obtained from metadata_repo
           at service construction time (cached), not hardcoded."""

   The parse_question method must NOT access DB at call time (§13 says
   "访问 DB=否"). Instead, at MetricService construction, it loads the
   list of supported metrics and dimensions from MetadataRepository and
   caches them. The parse logic uses these cached lists.

3. LLM Intent Planner (metric_rca/services/intent_planner.py)

   MVP uses an LLM planner for intent recognition. No keyword parser.

   class IntentPlanner(Protocol):
       """Protocol for intent parsing — allows alternative implementations
       for future multi-model routing without changing callers. Tests must
       not use mock/test-double planners in the current MVP."""
       def parse(
           self,
           question: str,
           *,
           business_today: date,
           supported_metrics: list[str],
           supported_dimensions: list[str],
           supported_families: list[str],
       ) -> ParsedIntent: ...

   class LLMIntentPlanner:
       """MVP implementation: LLM-based structured intent extraction.

       The LLM receives a system prompt containing:
       - The complete list of supported question families (6 types)
       - The complete list of supported metric_ids (from DB metadata)
       - The complete list of supported dimensions (from DB metadata)
       - The business_today date
       - Explicit constraints: "output MUST be one of these families",
         "metric_id MUST be one of these values", etc.

       The LLM returns structured output matching ParsedIntent schema.
       If the question does not match any supported family → PARSE_FAILED.
       If the question references an unsupported metric → METRIC_NOT_FOUND.
       If the question references an unsupported dimension → DIMENSION_NOT_ALLOWED.
       If the question references an unsupported date range → DATE_RANGE_INVALID.

       The LLM does NOT judge facts, write SQL, or decide root causes.
       It only extracts structured intent from natural language."""

       def __init__(
           self,
           *,
           model: str = "gpt-5.4-nano",
           api_key: str | None = None,
       ) -> None: ...

       def parse(
           self,
           question: str,
           *,
           business_today: date,
           supported_metrics: list[str],
           supported_dimensions: list[str],
           supported_families: list[str],
       ) -> ParsedIntent: ...

   Design constraints for LLM Intent Planner:
   - supported_metrics / supported_dimensions / supported_families are
     passed as parameters (from MetadataRepository), NOT hardcoded.
   - When adding a new metric to the DB and seeding it, the LLM planner
     automatically knows about it at next construction — zero code change.
   - Output is Pydantic-validated ParsedIntent (structured output /
     tool_use mode). Invalid LLM output → PARSE_FAILED, not silent retry.
   - LLM provider comes from settings (settings.llm_provider). If
     LLM is required but unavailable → LLM_REQUIRED_UNAVAILABLE.
   - Tests must exercise the real OpenAI planner. If the API key is
     missing or the model call fails, tests fail with LLM_REQUIRED_UNAVAILABLE.
   - The system prompt template should be a constant string with
     placeholders for the dynamic metadata lists. Do not hardcode
     metric names in the prompt template itself.

   MetricService.__init__ constructs LLMIntentPlanner from settings.
   If settings.llm_provider is missing or the API key is unavailable when
   parsing is required → LLM_REQUIRED_UNAVAILABLE.

   System prompt structure (example, adapt as needed):
     """You are an intent parser for a metric anomaly diagnosis system.
     Your job is to extract structured intent from user questions.

     SUPPORTED QUESTION FAMILIES:
     {families_list}

     SUPPORTED METRICS:
     {metrics_list}

     SUPPORTED DIMENSIONS:
     {dimensions_list}

     RULES:
     - Output MUST be valid JSON matching the ParsedIntent schema
     - metric_id MUST be one of the supported metrics
     - question_family MUST be one of the supported families
     - dimension MUST be one of the supported dimensions or null
     - target_date = business_today - 1 day (yesterday)
     - If the question does not match any family, set error: PARSE_FAILED
     - If the question mentions an unsupported metric, set error: METRIC_NOT_FOUND
     - If the question mentions an unsupported dimension, set error: DIMENSION_NOT_ALLOWED
     - If the question mentions a date range other than yesterday, set error: DATE_RANGE_INVALID
     """

4. Signal type mapping and root cause type mapping

   _SIGNAL_METRIC in fetch_related_signal.py:
   Move to a configuration or metadata source. For MVP, this can be
   a typed mapping in config/settings.py or a simple lookup table read
   from DB. The key constraint: it must not be hardcoded in the tool
   module as a module-level constant.

   _root_cause_type in attribution_service.py:
   This is closer to business rules than pure metadata. For MVP, extract
   to a typed configuration or a lookup function that can be overridden.
   Document it as a known simplification if it remains rule-based.

5. Seed data update (metric_rca/data/seed_data.py)

   _insert_metric_definitions must seed ALL metrics that the system
   supports at runtime: gmv, net_gmv, pay_cvr, refund_rate, uv, aov,
   stockout_rate, complaint_rate.

   Currently only 4 are seeded. The MetadataRepository will read from
   this table, so all 8 must be present.

6. Tool layer updates

   All 4 tools currently call get_metric_definition() as a free function.
   After refactor, they must receive MetricService (or MetadataRepository)
   via dependency injection and call the instance method.

   Tool function signatures change from:
     def detect_anomaly(args, *, repository, renderer, settings) -> ToolResult
   To:
     def detect_anomaly(args, *, repository, metadata_repo, renderer, settings) -> ToolResult
   Or:
     def detect_anomaly(args, *, repository, metric_service, renderer, settings) -> ToolResult

   The spy/mock pattern in tests must be updated accordingly.

─────────────────────────────────────────────────────────────────────────
SCOPE (ordered by priority)
─────────────────────────────────────────────────────────────────────────

1. Create metric_rca/repositories/metadata_repository.py
   - MetadataRepository with get_metric_definition, get_schema_context,
     list_metrics, list_dimension_values
   - Reads from metric_definition table (and optionally fact tables for
     dimension value discovery)
   - Typed errors: METRIC_NOT_FOUND, SCHEMA_CONTEXT_MISSING

2. Refactor metric_rca/services/metric_service.py
   - Remove METRIC_DEFINITIONS, SCHEMA_CONTEXT, _CHANNELS, _CATEGORIES
   - Remove ALL keyword matching logic (no if "gmv" in text chains)
   - MetricService class with metadata_repo + configured live LLMIntentPlanner
   - parse_question delegates to LLMIntentPlanner

3a. Create metric_rca/services/intent_planner.py
   - IntentPlanner Protocol
   - LLMIntentPlanner (MVP default: LangChain OpenAI wrapper with native structured output)
   - No mock planner test path; tests use real model calls.
   - System prompt template with metadata placeholders

3b. Update metric_rca/data/seed_data.py
   - Seed all 8 metrics into metric_definition table
   - Ensure consistency with renderer's METRIC_TEMPLATES

4. Update all 4 tool files to accept metadata_repo or metric_service
   - detect_anomaly.py, drilldown_dimension.py,
     fetch_related_signal.py, calculate_contribution.py

5. Extract _SIGNAL_METRIC from fetch_related_signal.py
   - Move to configuration or metadata-driven lookup

6. Update all P2 tests
   - SpyRepository or MockMetadataRepository for tool tests
   - New proof tests for Row 12/27 compliance

─────────────────────────────────────────────────────────────────────────
REQUIRED PROOF TESTS (must exist and pass before claiming fix complete)
─────────────────────────────────────────────────────────────────────────

1. test_services_and_tools_have_no_hardcoded_metric_definitions
   Source scan: metric_rca/services/ and metric_rca/agent/ must not contain
   "METRIC_DEFINITIONS" or MetricDefinition(...) object construction
   (imports and type hints are allowed).

2. test_services_and_tools_have_no_hardcoded_schema_context
   Source scan: no "SCHEMA_CONTEXT" constant in services or tools.

3. test_services_and_tools_have_no_hardcoded_dimension_values
   Source scan: no "_CHANNELS", "_CATEGORIES", or literal dimension value
   sets ("paid_ads", "organic", "electronics", etc.) in services or tools.

4. test_get_metric_definition_reads_from_metadata_repo_not_dict
   Mock MetadataRepository to return a custom MetricDefinition.
   Call MetricService.get_metric_definition → must return the mock's value.
   Prove it's not reading from a hardcoded dict.

5. test_drop_metric_from_metadata_repo_raises_metric_not_found
   MetadataRepository returns None for "gmv".
   Call MetricService.get_metric_definition("gmv") → METRIC_NOT_FOUND.

6. test_parse_question_uses_metadata_driven_metric_list
   Construct MetricService with a MetadataRepository that only has
   ["gmv", "pay_cvr"] and call the real OpenAI planner on a refund-rate
   question. MetricService.parse_question must raise METRIC_NOT_FOUND
   because refund_rate is not in the metadata repo, even though the planner
   extracted it.

6b. test_intent_planner_receives_metadata_context
   Construct real LLMIntentPlanner through MetricService. Call
   MetricService.parse_question and assert the parsed output is constrained
   by supported_metrics, supported_dimensions, and supported dimension values
   from the metadata repo, not hardcoded lists.

6c. test_no_keyword_parsing_in_metric_service
   Source scan: metric_service.py must not contain keyword-matching
   patterns: no 'in text', no 'if "gmv"', no 'if "refund"', no
   '_dimension_from_text', no '_element_from_text'.

7. test_seed_inserts_all_renderer_supported_metrics
   After make seed, query metric_definition table. Assert row count
   matches the number of metrics in renderer.METRIC_TEMPLATES.

8. test_metadata_repo_get_metric_definition_returns_typed_model
   Call real MetadataRepository (or integration test fixture) with a
   seeded metric_id → returns MetricDefinition with correct fields.

9. test_intent_planner_system_prompt_has_no_hardcoded_metrics
   Source scan: intent_planner.py must not contain literal metric_id
   strings ("gmv", "pay_cvr", "refund_rate", etc.) outside of test
   code. The system prompt template must use format placeholders only.

10. Retain all existing P2 tests (updated for new signatures).

─────────────────────────────────────────────────────────────────────────
FORBIDDEN
─────────────────────────────────────────────────────────────────────────

- No MetricDefinition(...) object construction in services/ or agent/
  (except in test fixtures).
- No module-level dicts of metric metadata in services/ or agent/.
- No seeded dimension values (channel names, category names) hardcoded
  in services/ or agent/.
- No keyword if/elif parsing chains in metric_service.py.
  No _dimension_from_text, no _element_from_text, no 'if "gmv" in text'.
- No breaking existing P1 tests (guard, schema, seed, repository).
- No weakening existing P2 proof tests.
- No fallback from MetadataRepository to hardcoded dict.
- No fallback from LLMIntentPlanner to keyword parser.
  If LLM is unavailable → LLM_REQUIRED_UNAVAILABLE, not silent degrade.
- No mock LLM planner in the test suite. Tests must use live OpenAI model
  calls and fail fast if the model is unavailable.
- No hardcoded metric/dimension/family lists in the LLM system prompt
  template. The template must use placeholders ({metrics_list}, etc.)
  that are filled dynamically from MetadataRepository at construction
  time. Adding a new metric to the DB must automatically make it
  available to the intent planner without any code change.

─────────────────────────────────────────────────────────────────────────
SETTINGS REQUIREMENTS
─────────────────────────────────────────────────────────────────────────

config/settings.py must support:
- llm_provider: str | None — "openai" (required for intent planner)
- llm_model: str — default "gpt-5.4-nano"
- llm_api_key: str | None — from env METRIC_RCA_LLM_API_KEY
- llm_required: bool — if True and provider unavailable → typed error
- llm_enabled: bool — must be True for MVP (intent planner needs it)

For tests: provide METRIC_RCA_LLM_API_KEY and exercise the real OpenAI
planner. Do not inject a mock planner.

COMMANDS
─────────────────────────────────────────────────────────────────────────

Run:
- make seed
- pytest -q tests/
- python -W error::ResourceWarning -m unittest discover -s tests -v

ACCEPTANCE CHECKS
- All items in docs/iteration-prompts/06-review-checklist.md pass.
- grep output for sections A1-A5 pasted in final response.
- Row 12 shortcut-to-avoid "service constants pretending to be DB-backed
  metadata" is provably absent.
- Row 27 shortcut-to-avoid "hardcoded metric/schema dictionaries" is
  provably absent.
- Known shortcuts: []

FINAL RESPONSE CONTRACT
Your final response must include:
1. Files changed
2. Tests added/updated
3. Commands run
4. Test output summary
5. Review checklist (06-review-checklist.md) results with grep output
6. Remaining deviations, mapped to matrix rows
7. Known shortcuts: []
If Known shortcuts is not exactly [], do not claim completion.
```
