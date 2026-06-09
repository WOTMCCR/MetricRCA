## ADL-0002: Intent Planner Uses LangChain OpenAI Wrapper Before Full LangGraph

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | fix-001-metadata-hardcode |
| 影响范围 | intent parsing, LLM provider dependency, future LangGraph integration |

### 背景与场景

The project will later implement a real LangGraph `StateGraph` for the complete RCA workflow. The current iteration only needs the intent parsing LLM call, but hand-written OpenAI HTTP request and response parsing added unnecessary local protocol code.

### 决策

Keep `LLMIntentPlanner` as the domain service boundary and implement its OpenAI call through `langchain_openai.ChatOpenAI.with_structured_output(..., method="json_schema")`. Do not introduce a one-node LangGraph graph for this P2 iteration. Add `httpx[socks]` because this environment routes external API calls through a SOCKS proxy.

### 理由

LangGraph should own multi-step RCA state orchestration, routing, reducers, repair loops, and termination policy. A single `START -> parse_question -> END` graph would not satisfy the documented P3 graph contract and would add ceremony without orchestration value. LangChain's model wrapper removes hand-written OpenAI response traversal while keeping the planner directly reusable inside a future LangGraph node.

### 被否决的方案

Keeping the raw `urllib` Responses API call was rejected as unnecessary client plumbing. Introducing LangGraph only around intent parsing was rejected because it would look like graph adoption without the real P3 RCA graph behavior.

### 后续跟进

When P3 lands, implement `agent/graph.py` with a real `StateGraph(RCAState)` and make the parse node call `MetricService.parse_question(...)` instead of duplicating model client logic in the graph node.

## ADL-0001: Metadata Contracts Move Behind Repository And Planner Boundaries

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | fix-001-metadata-hardcode |
| 影响范围 | metric metadata, metric service, intent parsing, tool dependency injection |

### 背景与场景

`metric_service.py` duplicated metric definitions, schema context, and seeded dimension values as runtime constants. The same module also parsed questions with keyword branches, which conflicted with the DB-backed metadata and LLM-assisted intent parsing contracts.

### 决策

Metric metadata reads go through `MetadataRepository`, while `MetricService` caches supported metrics and dimensions at construction and delegates natural-language parsing to a configured live `LLMIntentPlanner`. Tool modules receive `metric_service` explicitly instead of importing free metadata functions.

### 理由

This preserves the documented boundary: metadata is persisted and DB-backed, parse intent does not access DB at call time, and parser tests exercise the real OpenAI intent planner instead of a mock planner. Adding a metric to `metric_definition` becomes visible to the parser context without changing runtime service constants.

### 被否决的方案

Keeping keyword parsing as a fallback was rejected because it would silently bypass the required LLM planner. Keeping service-level metadata constants was rejected because persisted metadata mutations would not affect runtime behavior.

### 后续跟进

Future graph/node work must construct `MetricService` with a real `MetadataRepository` and configured LLM provider settings.
