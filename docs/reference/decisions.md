## ADL-0045: Signal-first intent priority for multi-day GMV drift framing

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | GPT nano provider validation |
| 影响范围 | LLM intent planner prompt, RCA signal selection |

### 背景与场景

`gpt-5-nano` validation passed intent and anomaly checks for C28
`GMV has been declining since the weekend`, but sometimes selected the generic
channel top contributor path. That made `fetch_related_signal` bind to paid ads
instead of the intended organic channel signal anomaly. The expected behavior is
still a single configured target-date RCA, but with signal-first channel element
selection for multi-day drift wording.

### 决策

The intent planner prompt now explicitly maps unscoped GMV multi-day drift
framing such as "has been declining", "since the weekend", and "over the
weekend" to `analysis_strategy=signal_first`. The prompt also states that the
target date remains the configured single run target date while signal-first
discovery selects the channel element with the strongest related signal anomaly.

### 理由

This preserves the LLM-first intent boundary and keeps all downstream execution
deterministic. The PlanCompiler already has a `signal_first` policy that uses
the structured intent to select the signal-anomalous channel element; no runtime
question-text matching is needed.

### 被否决的方案

- Adding a runtime branch for "since the weekend": this would be a forbidden
  Python natural-language mapper.
- Making rank_root_causes infer multi-day context: the ranker consumes evidence
  and should not reinterpret original user wording.

---

## ADL-0044: Product-first intent priority for merchandise sales questions

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | GPT nano provider validation |
| 影响范围 | LLM intent planner prompt, RCA plan selection |

### 背景与场景

`gpt-5-nano` provider validation exposed an intent instability for broad GMV
questions that use merchandise wording. The question "Why did yesterday's GMV
decline in merchandise sales?" kept the correct metric but sometimes selected a
channel-first strategy, causing the deterministic plan to fetch campaign
signals first and rank a channel/campaign cause over the expected product/AOV
cause.

### 决策

The intent planner prompt now states that product, merchandise, SKU, item,
price, AOV, basket-size, and average-order-value wording takes priority over
broad store/channel defaults. Those questions should use
`analysis_strategy=product_first` unless the user explicitly says merchandising
was stable or explicitly asks about channel/campaign traffic.

### 理由

This keeps natural-language semantic resolution inside the LLM intent planner,
as required by the architecture red lines, while making the structured intent
less provider-sensitive. The deterministic PlanCompiler continues to consume
only `ParsedIntent` and metadata-backed policy; no Python keyword mapper was
added.

### 被否决的方案

- Adding a Python branch for "merchandise sales": this would violate the
  LLM-first intent boundary.
- Changing the ranker to prefer product for this phrase: the ranker does not
  receive natural language and should not encode question wording.

---

## ADL-0043: Explicit LLM temperature only

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | OpenAI Agents SDK provider compatibility |
| 影响范围 | Settings, AgentRuntimeConfig, OpenAIAgentsRuntime |

### 背景与场景

Provider compatibility testing with OpenAI `gpt-5-nano` showed that the model
rejects an explicit `temperature` request parameter. MetricRCA previously
defaulted `llm_temperature` to `0.0`, which made the runtime send a parameter
that was not required for deterministic intent parsing and is unsupported by
some provider/model combinations.

### 决策

`Settings.llm_temperature` now defaults to `None`. The runtime passes no
temperature value unless the operator explicitly configures
`METRIC_RCA_LLM_TEMPERATURE`. Explicit temperature values are still threaded into
`AgentRuntimeConfig` and the Agents SDK model settings unchanged.

### 理由

Omitting an optional provider parameter is not a fallback path: the model,
provider, key, structured-output method, and tracing configuration remain
explicit and fail-fast. It avoids rejecting otherwise valid OpenAI models while
preserving operator control for providers that support deterministic temperature
configuration.

### 被否决的方案

- Hardcoding a model-name allowlist for temperature support: this would add a
  runtime provider heuristic that must track external model behavior.
- Catching the provider error and retrying without temperature: this would be a
  silent fallback and would hide unsupported configuration from tests and ops.

---

## ADL-0042: Async AgentRuntime and metadata-backed plan family routing

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | OpenAI Agents SDK migration post-review fixes |
| 影响范围 | AgentRuntime, OpenAIAgentsRuntime, metric_definition, RcaPlanCompiler, runtime tooling |

### 背景与场景

Post-review P1 findings identified two production-readiness gaps. First,
`OpenAIAgentsRuntime.run_structured()` used the Agents SDK sync runner, which is
valid for the current synchronous eval path but unsafe as the only supported
contract for async hosts. Second, `RcaPlanCompiler` classified metric families
with an in-code rate metric set, requiring runtime code edits for catalog growth.

The review also noted maintainability debt in runtime dependency typing and the
large `sdk_tools.py` ranking section.

### 决策

`AgentRuntime` now exposes both `run_structured()` and `arun_structured()`.
`OpenAIAgentsRuntime.arun_structured()` calls the SDK async `Runner.run()` path
directly; sync callers continue to use `Runner.run_sync()` through
`run_structured()`.

`metric_definition` now carries `metric_family` as DB-backed metadata. The
metadata repository hydrates it into `MetricDefinition`, and
`RcaPlanCompiler` requires a metric metadata provider rather than maintaining a
hardcoded `RATE_FAMILY_METRICS` list. The legacy subagent router was updated to
read the same metadata field.

`RcaPlanCompiler` no longer abbreviates dimensions through a static
`_dimension_prefix()` map for planned E3 outputs. Planned E3 outputs declare
only the stable `E3` family alias; persisted concrete E3 evidence aliases remain
produced by the tool layer for backwards-compatible evidence matching.

The `metric_family` metadata column is `NOT NULL` without a DB default. Seed and
test rows must provide it explicitly so missing family metadata fails at the
metadata boundary instead of silently routing to a default family.

Runtime dependency bags are now documented with Protocols, and persisted E4
ranking/adtributor logic moved from `runtime/sdk_tools.py` to
`runtime/ranking.py`.

### 理由

The async method keeps SDK event-loop semantics explicit without requiring
RunService to become async. Metadata-backed family routing aligns plan
compilation with the project red line that metric facts come from DB-backed
metadata, while keeping natural-language intent mapping in the LLM prompt.

The ranking extraction is behavior-preserving but makes tool registration and
ranking easier to review independently. E4 summary update persistence is now a
required repository method instead of a row-mutation compatibility path.

### 被否决的方案

- Wrapping `Runner.run_sync()` in an async helper: it preserves the event-loop
  hazard instead of removing it.
- Inferring rate family from metric names, suffixes, or question family text:
  this would replace one hardcoded route with another runtime heuristic.
- Adding a default metric family in `MetricDefinition`: this would mask missing
  metadata instead of failing at the boundary.

---

## ADL-0041: Production follow-ups for sync AgentRuntime and metadata-driven planning

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | superseded by ADL-0042 |
| 关联迭代 | OpenAI Agents SDK migration post-review |
| 影响范围 | AgentRuntime, OpenAIAgentsRuntime, RcaPlanCompiler |

### 背景与场景

This entry records the initial post-review follow-up decision. ADL-0042 later
implemented these follow-ups in code.

Post-migration review accepted the Phase B result but flagged production follow-ups:
`OpenAIAgentsRuntime.run_structured()` currently calls `Runner.run_sync()`, which
is safe for the current synchronous eval/API runner path but must not be called
from an already-running event loop. If MetricRCA is embedded directly in an async
framework handler, the runtime boundary needs an async method instead of nesting
the SDK sync runner.

The same review noted two metadata-adjacent planning constants:
`RATE_FAMILY_METRICS` in `RcaPlanCompiler` and `_dimension_prefix()` action-id
abbreviations. These are structural planning choices, not metric definitions or
natural-language parsing, but they require code changes when the metric catalog
or dimension catalog grows.

### 决策

For the accepted Phase B implementation, `AgentRuntime` remains explicitly
synchronous. This is a documented constraint: callers must run it from the sync
RCA runner path or offload it to a worker thread/process when invoked from async
application code. A production async API path must extend the protocol with an
async structured-output method and implement it with the Agents SDK async runner
rather than wrapping `Runner.run_sync()` inside an event loop.

`RATE_FAMILY_METRICS` and `_dimension_prefix()` are accepted as Phase B
structural constants only. Before adding new metric families or dimensions in
production, family classification and action-id prefixing should be compiled from
DB-backed metric/dimension metadata or a validated planning policy registry.
They must not grow into hardcoded metric definitions, alias maps, or
natural-language intent parsers.

### 理由

The current system has proven 28/28 Phase B behavior on the synchronous eval path
and keeps the OpenAI SDK isolated behind `AgentRuntime`. Adding async semantics
now would change runtime contracts after final validation and requires its own
tests. Recording the constraint prevents accidental FastAPI/event-loop embedding
from being treated as supported.

The planning constants do not bypass QuerySpec → SQLRenderer → SQLGuard →
Repository and do not resolve natural language. Still, catalog growth should be
metadata-driven so future metrics and dimensions do not require runtime code
edits for basic classification.

### 被否决的方案

- Calling `Runner.run_sync()` from async FastAPI handlers: risks event-loop
  deadlock or runtime errors.
- Adding an untested async method in this Phase B closeout: changes the accepted
  runtime contract after final eval.
- Moving metric family detection into keyword prompt parsing: violates the
  LLM-first and metadata-path red lines.

---

## ADL-0040: Agents SDK provider requests use explicit timeout and retry bounds

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | OpenAI Agents SDK migration / Phase B eval optimization |
| 影响范围 | OpenAIAgentsRuntime, AgentRuntimeConfig |

### 背景与场景

DeepSeek confirm run once stalled inside `Runner.run_sync` while waiting on the
OpenAI-compatible model request. `AgentRuntimeConfig` already carried
`timeout=30` and `max_retries=0`, but the Agents SDK adapter did not pass those
values into the SDK provider client, so the configured failure boundary was not
actually enforced.

### 决策

`OpenAIAgentsRuntime` now constructs an explicit `AsyncOpenAI` client with
`api_key`, `base_url`, `timeout`, and `max_retries`, then passes that client to
`OpenAIProvider`. The business-facing runtime boundary remains unchanged:
`RunService`, `PlanCompiler`, and `ToolExecutor` still depend only on
`AgentRuntime` abstractions and never import SDK types.

### 理由

External LLM calls must fail fast with typed runtime errors instead of hanging an
eval or production RCA run. Wiring the existing config into the SDK client keeps
provider behavior explicit and testable without adding fallback or retry policy
outside the configured values.

### 被否决的方案

- Leave SDK defaults in place and rely on manual interruption: not a production
  failure boundary.
- Catch timeout and silently switch provider/model: violates Zero Fallback.
- Add timeout handling in RunService or PlanCompiler: would leak provider
  concerns across the AgentRuntime boundary.

---

## ADL-0039: Phase B deterministic ranking and reflection repairs

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | Phase B eval optimization |
| 影响范围 | runtime sdk tools, calculate_contribution, rank_root_causes, reflection |

### 背景与场景

28-case eval 暴露出若干非意图层缺口：C09 的非 top-channel related-signal evidence
会被 Adtributor 第一名覆盖；C24 正向 GMV spike 被 AOV drop 改写；C25 `refund_rate`
discovery 需要根据 `refund_quality` signal anomaly 选择 product；rate metric 的低
coverage 贡献在有匹配 signal 时不应触发 additive metric 的覆盖率 repair。

### 决策

rank 阶段保留由匹配 E3 signal 验证过的 selected candidate，不让 Adtributor 增强结果
无条件覆盖它。`signal_first` 动态 selection 使用已通过 QuerySpec → SQLRenderer →
SQLGuard → Repository 的确定性 current/baseline signal 查询选择 anomaly severity
最强的 candidate；`refund_quality` 动态 selection 使用同一受控数据路径选择当前
signal level 最高的 candidate。不在 runtime 中硬编码 dimension element。
`calculate_contribution` 动态步骤继承 E3 的 dimension/element，避免重新
退回 E2 top candidate。AOV drop rewrite 只在负向异常下生效；rate metric 在有 matching
signal evidence 时允许低 coverage 通过 Reflection。

### 理由

根因排序必须尊重当前 run 的证据链，而不是只尊重第一条贡献候选。正向 spike 与 rate
metric 的解释机制不同于 additive GMV drop，统一套用 drop/coverage 规则会产生错误
repair 或错误 root cause。

### 被否决的方案

- 按 case_id 或 ground truth 覆盖 selected candidate：违反 eval integrity。
- 对 rate metrics 放宽所有 Reflection 校验：会允许无 signal 支撑的低质量解释通过。
- 在工具外直接查询事实表：违反固定数据访问路径。

---

## ADL-0038: Run target date is explicit LLM intent context

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | Phase B eval optimization |
| 影响范围 | MetricService, LLMIntentPlanner intent prompt |

### 背景与场景

C22 `Was GMV abnormal two days ago?` 在 eval runner 中使用 `target_date=2026-06-03`
和 `business_today=2026-06-04`。DeepSeek 按自然日期算术把 “two days ago”
解析成 2026-06-02，导致系统在 spike date 上正确检测到异常，但与 C22 的
no-anomaly run context 不一致。修改 eval runner 或 cases.jsonl 被 Phase B 红线禁止。

### 决策

`MetricService` 将配置中的 `target_date` 作为 `run_target_date` 传入
`LLMIntentPlanner`。intent prompt 明示 `RUN TARGET DATE` 是本次 RCA 运行的配置分析日：
当问题使用 relative-date wording 或泛化异常/变化表达且没有显式 calendar date 时，
LLM 必须将输出 `target_date` 设为该 run target date；若用户给出 “on the Nth”
这类显式 calendar date，则使用显式日期。语义解析仍完全由 LLM structured output 完成，
Python 只传递结构化 run context，不做关键词/regex date mapper。

### 理由

eval、API 和 runtime 已经都有目标分析日概念；把该上下文提供给 LLM 可以消除 provider
对相对日期的不同算术解释，同时不改变 harness、scorer 或 ground truth。它也让生产 API
中显式传入的 target_date 成为意图解析上下文，而不是只在后续工具层出现。

### 被否决的方案

- 修改 eval runner 的 `business_today` 推导：违反 eval harness 红线。
- 在 Python 中识别 “two days ago” 并改写日期：违反 LLM-first intent 红线。
- 在 prompt 中硬编码 C22 原题到固定日期：过拟合 eval 题面，且不适用于其它 run target。

---

## ADL-0037: DeepSeek uses explicit Agents SDK json_mode with safe tracing

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | OpenAI Agents SDK migration / Phase B eval optimization |
| 影响范围 | AgentRuntimeConfig, OpenAIAgentsRuntime, Settings |

### 背景与场景

DeepSeek OpenAI-compatible endpoint 拒绝 Agents SDK 默认的 `json_schema`
`response_format`，返回 `This response_format type is unavailable now`。Phase B 同时
要求可开启 Agent Traces，并验证切到 DeepSeek 模型后 tracing 是否仍有效。

### 决策

`llm_structured_output_method=json_mode` 成为显式配置路径：Agent 仍由 OpenAI Agents SDK
执行，但 output_type 设为 plain text，并通过 prompt 附加 JSON schema；runtime 使用
Pydantic `TypeAdapter.validate_json` 将文本校验成目标结构化模型。DeepSeek json_mode 通过
`ModelSettings.extra_body={"response_format":{"type":"json_object"}}` 明确启用。Tracing
通过 `agent_tracing_enabled` 和 `agent_trace_group_id` 配置接入 `RunConfig`，并始终设置
`trace_include_sensitive_data=False`；hosted trace export 依赖 `OPENAI_API_KEY`，模型调用
可使用 DeepSeek key。

### 理由

这是显式 provider 配置，不是在 `json_schema` 失败后 fallback。无效 JSON 会以
`MODEL_BEHAVIOR_ERROR` typed error 失败并由 intent planner 的既有 parse retry 处理。
Tracing 属于 SDK runtime 观测层，和模型 provider 解耦，但不能把用户输入/输出敏感数据写入
trace。

### 被否决的方案

- 捕获 DeepSeek `json_schema` 失败后自动改用 json_mode：provider fallback。
- 在业务层直接调用 OpenAI SDK 或 DeepSeek client：破坏 AgentRuntime 边界。
- 开启 sensitive trace data：不需要且增加泄露风险。

---

## ADL-0036: OpenAI Agents SDK behind AgentRuntime boundary

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-17 |
| 状态 | accepted |
| 关联迭代 | OpenAI Agents SDK migration |
| 影响范围 | intelligence runtime, MetricService, LLM client |

### 背景与场景

用户要求从 LangChain/DeepAgents runtime 迁移到 OpenAI Agents SDK，同时明确禁止
`RunOrchestrator`、`PlanCompiler`、`ToolExecutor` 直接依赖 OpenAI SDK。旧实现还依赖
LangGraph 内部结构做 tool 泄漏检查，架构边界脆弱。

### 决策

新增 provider-neutral `AgentRuntime` Protocol 与 `AgentRuntimeConfig`。业务层只依赖
`run_structured(...)` 抽象；OpenAI Agents SDK 相关 import、`Agent`、`Runner`、
`OpenAIProvider` 和 `RunConfig` 只存在于 `openai_agents_runtime.py` adapter。配置层显式
要求 provider/model/api_key/base_url，非 OpenAI provider 不允许读取 `OPENAI_API_KEY`
作为替代 key。unsupported provider、缺 key/base_url、unsupported structured output method
均 typed fail-fast。

### 理由

这样可在不污染核心 RCA 编排、plan compiler 和 deterministic tool executor 的前提下使用
Agents SDK structured output、tracing 和 provider adapter 能力。SDK 替换或第三方
OpenAI-compatible provider 差异被限制在 runtime adapter 内。

### 被否决的方案

- 在 MetricService 或 RunService 直接实例化 `agents.Agent`：违反用户架构红线。
- 保留 LangChain/DeepAgents compatibility layer：继续携带旧 runtime 的内部结构依赖。
- provider 缺失时默认切回 OpenAI：违反 Zero Fallback。

---

## ADL-0035: Phase B eval-driven PTV optimization loop

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | Phase B eval optimization |
| 影响范围 | intent prompt, anomaly detection direction, expert prompt guidance |

### 背景与场景

28-case eval harness（ADL-0034）已构建完成，预期 8 个新 case 中约 4-6 个会失败。
阶段 B 使用 PTV（Predict-Then-Verify）自动循环修复系统能力缺口。

### 决策

采用 PTV 自动循环模式（最多 6 轮），每轮：predict → eval → gap analysis → minimal fix。
修复类型分为 FIX-I（intent prompt）、FIX-T（tool/service）、FIX-P（expert prompt）、
FIX-G（guard logic），按 gap_report divergence 类型驱动。

架构红线：eval harness 不可改（cases/scorer/injection/ground_truth）；自然语言语义解析
只走 LLM intent prompt（禁止 Python keyword/regex parser）；数据/元数据路径不变；
原 20 case 每轮零回归。

验收门槛：28/28 连续 2 次 green；intent/anomaly 28/28；top1≥85% top3≥93%。

### 被否决的方案

- 手动逐 case 修复（无 PTV 预测对照）：失去科学实验的预测-验证结构。
- 分多个 prompt 手动迭代：Codex 可自主循环，减少人工干预延迟。
- 降低门槛适配新 case：违反 eval integrity 原则。

---

## ADL-0034: Eval harness expansion from 20 to 28 cases

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | Eval harness expansion |
| 影响范围 | anomaly injection, seed ground truth, eval case library, scorer |

### 背景与场景

P9 的 20-case eval 已覆盖核心 GMV/rate/no-anomaly/multi-dimension 路径，但仍有三类
评估空洞：已存在注入却没有题面的指标覆盖、日期/方向鲁棒性挑战、以及组合/时间漂移结构复杂度。
本阶段只扩展 eval harness，不修 agent、service、API、intent 或工具行为；新增 case 失败应作为后续
优化阶段的系统能力缺口，而不是在本阶段改题面、阈值或评分迁就。

### 决策

将 eval case library 从 20 扩到 28：

- Coverage gaps：C21 `pay_cvr` discovery、C23 `uv` target metric、C25 `refund_rate`
  discovery，均复用已有 TARGET_DATE 注入。
- Robustness challenges：C22 2026-06-03 paid_ads borderline GMV no-anomaly、C24
  2026-06-02 paid_ads 正向 spike、C26 模糊 sales intent，验证日期/方向/意图鲁棒性。
- Structural complexity：C27 复用 TARGET_DATE 的 paid_ads + electronics 组合主因，并在
  scorer 中要求 `dimension_elements` 同时包含 `("channel","paid_ads")` 与
  `("category","electronics")`；C28 添加 TARGET_DATE 前 organic UV 渐进式 drift，但仍按
  TARGET_DATE 单日评分。

`anomaly_injection.py` 只新增 `BORDERLINE_DATE`、`SPIKE_DATE` 和 organic drift 乘数，不改既有
TARGET_DATE 分支。Organic drift 乘数按 seed 后实测调弱为 2026-06-03 `0.95`、
2026-06-04 `0.945`，以保证 C22 与 06-04 no-anomaly traps 的 GMV z-score 均低于 2.0。
`scorer.py` 只把 C22 纳入 no-anomaly traps，并把 C27 纳入多维组合断言。

### 理由

把 coverage/robustness/structural 三类 case 放进同一 harness，可以在不修系统行为的前提下暴露
后续优化优先级：自然语言 traffic→UV、正向异常检测、ambiguous intent、组合主因证明和单日系统对
multi-day framing 的处理。C22 与 06-04 no-anomaly overlap 必须通过 seed 后数据查询验证，防止新增
organic drift 破坏已有 no-anomaly trap。

### 被否决的方案

- 在本阶段修 intent planner、agent prompt 或工具策略：会混淆“eval harness 扩容”和“系统能力优化”。
- 改 anomaly thresholds 或 scorer 阈值来适配新增 case：会削弱 eval integrity。
- 调整既有 20 条 ground truth：会破坏 P7/P9 可比性。

### 后续跟进

下一优化阶段可运行 eval 并基于 trace 判断新增 case 的真实失败原因；预期 C23/C24/C26/C28
等 case 可能暴露 intent、正向异常或 temporal framing 能力缺口。

---

## ADL-0033: P9 multi-agent unknown metric and expert factory failures fail fast

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P9 multi-agent final |
| 影响范围 | multi-agent routing, agent factory, operations docs |

### 背景与场景

P9 对抗审查指出 multi-agent triage 使用固定 Phase 1 family 集合：
`gmv/net_gmv/uv/aov` 路由到 `gmv_family`，`pay_cvr/refund_rate/stockout_rate/complaint_rate`
路由到 `rate_family`。如果未来 intent planner 解析出尚未纳入 Phase 1 或尚未分配 expert family 的
metric_id，multi-agent path 会返回 `METRIC_NOT_FOUND`。审查还指出当 `multi_agent_enabled=true`
且某个 expert 构建失败时，factory 会让整个 bundle 构建失败，而不是退回 single-agent。

### 决策

保留 fail-fast 行为，不引入 generic expert 或 single-agent downgrade：

- triage 只接受 `PHASE1_METRICS` 且已显式分配 family 的 metric_id；未知 metric 或未分配 family 的
  metric 失败为 `METRIC_NOT_FOUND`。
- `multi_agent_enabled=true` 时，GMV/rate experts 必须全部构建成功、暴露同一工具集、共享同一
  middleware/context；任一 expert 构建或工具集校验失败，run 失败为 typed factory error，不降级为
  P8 single-agent path。
- HTTP eval 的并行 worker 使用 per-thread `httpx.Client(trust_env=False)`；这是本地 HTTP 隔离和线程边界，
  不是 provider 或 API fallback。

### 理由

本项目的 P0 规则禁止 silent fallback。未知 metric 或 expert 构建失败时退回 generic/single-agent 会让
配置错误在生产中变成“看似成功但 topology 不符合配置”的运行结果，并可能绕过 P9 的路由/共享预算证明。
新增 metric 必须先进入 metadata/intent/eval 约束，再被显式分配到 expert family。Multi-agent 配置失败
应在请求路径上以 typed error 暴露，便于运维发现并修复配置，而不是自动降级。

### 被否决的方案

- 未知 metric 自动调用 generic agent：会把未设计的 metric family 当作已支持能力。
- multi-agent expert 构建失败时退回 single-agent：违反 `multi_agent_enabled=true` 的显式配置语义和
  zero-fallback 规则。
- 在 triage 中用 LLM 猜测未知 metric 的 family：重复 intent parsing 边界，并引入不可审计路由。

### 后续跟进

新增 metric（例如 `cart_abandon_rate`）时必须更新 metric metadata、intent/eval case、family routing
测试和本文档/合规矩阵。若未来需要第三个 family，应新增显式 expert，而不是复用 fallback path。

---

## ADL-0032: P9 RunOutcome advisory validation and parallel memory prepass

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P9 multi-agent final |
| 影响范围 | RunOrchestrator, eval runner, multi-agent tests |

### 背景与场景

P9 初版把 expert `RunOutcome` 作为强校验：malformed outcome 直接使 run 失败。但 P6-P8 的
核心事实状态一直由 persisted Evidence、Reflection 与 deterministic projector 决定；如果让
RunOutcome 驱动失败，就会把一个 eval/trace 辅助结构提升为生产事实来源。同时 P8 已通过
`memory_write_on_finalize=false` 隔离 eval memory prepass 的 case 间写污染，memory leg 不再有
顺序执行依赖。

### 决策

`RunOutcome` 只作为 advisory structured output：RunOrchestrator 可记录 malformed/mismatched
outcome warning，但不得用它投影报告、替代 Reflection、或覆盖 persisted artifact flow。缺失或
malformed RunOutcome 不再使 run 失败；真实终态仍由当前 run 的 persisted Evidence、Reflection
结果、no-anomaly contract 与 report projector 决定。Multi-agent path 继续由 triage trace
`node=triage/action=route_{family}` 推导。

Direct eval 的 memory prepass 复用 `_run_cases` 的 `ThreadPoolExecutor` 路径；新增
`memory_enabled` 与 `memory_write_on_finalize` 参数，使 `METRIC_RCA_EVAL_CONCURRENCY`
同时控制 memory-enabled prepass 与 baseline phase。Memory prepass 仍强制
`memory_write_on_finalize=false`，所以并发 case 只读取 seed/pre-existing memory，不会互相写入
episodic/reflection 污染。

HTTP eval 同样使用一个并行 case runner core：`--concurrency` / `HTTP_CONCURRENCY` /
`METRIC_RCA_EVAL_CONCURRENCY` 同时控制 memory prepass 与 baseline phase。HTTP memory leg
通过 `/api/rca/runs` per-request override 传 `memory_write_on_finalize=false`，因此不需要串行执行来避免
case 间污染。

### 理由

RunOutcome 可以帮助调试 expert 是否自认为完成，但不能成为事实源；否则会违反 ADL-0006 的
persisted artifact projection。并发 memory prepass 与 P8 的写隔离相容，可显著缩短 P9 eval
反馈时间，同时保持 eval 读写污染边界。

### 被否决的方案

- malformed RunOutcome fail-fast：会让非事实辅助输出掩盖 DB 中已经完整的 Evidence/Reflection。
- 用 RunOutcome 字段生成 report 或 candidate：绕过 persisted Evidence 和 deterministic projector。
- memory prepass 继续固定顺序：没有安全收益，且使 P9 eval 开发反馈过慢。
- memory prepass 并发但允许写 memory：会重建 P8 已修复的 case 间 episodic/reflection 污染。
- HTTP eval 保持串行：会让 API-only acceptance 与 direct eval 在调度语义上分叉，且反馈过慢。

### 后续跟进

Differential tests 只断言结构等价、triage trace、共享 budget、no-anomaly contract 与 repair flow；
不要求 multi-agent LLM 输出与 single-agent top1 完全一致。

---

## ADL-0031: P9 multi-agent 只按 ParsedIntent 路由并共享 run 级 guard budget

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P9 multi-agent final |
| 影响范围 | RunOrchestrator, agent factory/subagents, eval scorer, trace observability |

### 背景与场景

最终版要求在保持 P8 单 expert 行为不变的前提下，新增开关式 multi-agent topology：
triage 负责把已解析 intent 路由给 GMV family 或 rate family expert，expert 继续通过同一套
受 GuardMiddleware 约束的工具链完成 evidence loop。风险在于 triage 若重新解析自然语言、
subagent 若复制 guard/middleware/budget，都会引入与 P6-P8 决策冲突的第二套规划或预算边界。

### 决策

`multi_agent_enabled=false` 时继续使用 P8 single-agent path。开启时，RunOrchestrator 在
LLM intent planner 产生 `ParsedIntent` 后执行确定性 triage：只读取 `ParsedIntent.metric_id`
并按 Phase 1 metric family 映射到 `gmv_family` 或 `rate_family`；未知指标 fail-fast 为
`METRIC_NOT_FOUND`。Triage 写 trace step（node=`triage`, action=`route_{family}`），不消耗
data-tool step/query/drilldown budget。Expert subagents 共享同一个 tool set、同一个
GuardMiddleware、同一个 `RunGuardContext`，因此预算与 repair guard 均为 run 级。Expert 完成后
必须返回结构化 `RunOutcome`；malformed outcome 统一失败为 `AGENT_INVOKE_FAILED`。Eval scorer
记录 `multi_agent_path`，取值为 `single_agent` 或 `multi_agent:{family}`，summary 统计路径分布。

> ADL-0032 supersedes the malformed-RunOutcome failure behavior: RunOutcome is
> advisory and malformed output logs a warning while persisted artifacts remain
> authoritative.

### 理由

Intent planner 已经是自然语言解析边界，triage 重新解析 question 会违反 ADL-0013 并可能造成
target metric drift。Guard budget 是 run 级安全约束，不能因进入另一个 expert 而重置。结构化
RunOutcome 让 expert 输出只表达状态与 evidence 引用，最终事实仍来自 persisted Evidence、
Reflection 和 deterministic projector。

### 被否决的方案

- 让 triage LLM 从原始 question 重新判断 family：重复自然语言语义边界，且会绕过 ParsedIntent。
- 为每个 expert 创建独立 GuardMiddleware/context：会重置预算并允许跨 expert 放大 tool calls。
- 让 expert 返回自由文本结论：会绕过 ADL-0006 的 persisted artifact projection。
- multi-agent 默认开启：会破坏 P8 acceptance 的 single-agent baseline 与差分测试基线。

### 后续跟进

P9 differential tests 必须证明 on/off score 字段一致、GMV/rate 路由正确、预算共享、
no_anomaly contract 与 Reflection repair 在 multi-agent 模式下保留。

---

## ADL-0030: Production discovery 可读取无 scope 的 run memory

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | RunOrchestrator memory read scope, eval memory isolation |

### 背景与场景

P8 修复了 direct eval memory prepass 的 case 间污染：eval memory leg 读取 seed/pre-existing memory，
但不写 episodic/reflection run memory。对抗审查指出 production discovery run 的 `scope={}` 仍会读取
`metric|run` 下无 filters 的 episodic/reflection 命中，后续 discovery run 可能看到历史 run context。

### 决策

保留 production 行为：discovery scope 可以读取无 filters 的 trusted run memory；scoped case 只读取
同 scope 命中。Eval 通过 `memory_write_on_finalize=false` 做读写隔离，避免同一轮 eval case 相互污染。

### 理由

Production memory 是规划输入，用于影响 drilldown priority 和历史 context；无 scope discovery 没有
更窄过滤条件，读取无 filters 的同 metric run memory 是有意设计。Memory 仍不能成为 final conclusion，
报告和评分必须依赖当前 run evidence。Eval 的目标是测 memory read influence，不是让 eval case 互相训练，
所以 eval 采用单独的写隔离。

### 被否决的方案

- 在 production discovery 中丢弃所有无 filters 的 episodic/reflection memory：会让历史 discovery
  经验无法影响规划优先级，削弱 P8 memory v2 的生产用途。
- 对 production 使用 eval snapshot 隔离：会把 eval-only 边界带入正常运行，且增加 repository/query 复杂度。
- 允许 scoped case 读取无 filters run memory：会把宽 scope 历史提示注入明确 slice 问题，边界过宽。

### 后续跟进

P9+ 可评估 confidence decay、TTL、target_date/time-window filtering，避免长期历史过量影响 discovery。

---

## ADL-0029: Direct eval memory prepass 只读 memory，不写 run memory

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | Settings, direct eval runner, RunOrchestrator memory writes |

### 背景与场景

Direct eval 的 memory-enabled prepass 会顺序运行全部 case。每个成功 case 若写入 episodic memory，
后续无显式 scope 的 discovery case 会读取前序 case 的 `metric|run` memory，导致 LLM  shortcut
当前 evidence discovery，失败重试还会继续追加 reflection/episodic 污染。

### 决策

新增 `memory_write_on_finalize` 配置，默认开启。Direct eval 的 memory prepass 使用
`memory_enabled=true` 读取 seed semantic memory，但设置 `memory_write_on_finalize=false`，
RunOrchestrator 在该模式下跳过 episodic 与 reflection finalize memory 写入。

### 理由

P8 eval 要验证 memory context 对当前 run 的读取影响，而不是让同一轮 eval case 之间相互训练。
读写隔离避免复杂 snapshot tracking，同时保持正常产品运行的 memory 写入语义不变。

### 被否决的方案

- 在 memory read 时维护 eval 开始前的 memory id snapshot：更精确但增加 runner/repository 耦合。
- 每个 memory case 后清理新增 memory：需要 destructive cleanup 语义，且容易误删非 eval 记录。
- 关闭 `memory_enabled`：会绕过 P8 memory retrieval，不能验证 semantic memory 注入。

### 后续跟进

Direct eval 完成前先 `make seed`，确保 pre-existing memory 只包含 seed semantic records。

---

## ADL-0028: Reflection no-evidence repair 与 no-anomaly pre-handler gate

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | reflection verifier, GuardMiddleware, RunOrchestrator, eval scorer |

### 背景与场景

Predict-Then-Verify `ptv3` 在 memory-enabled C06 暴露了一个 flow 缺口：agent 读取 memory 后只产生
`llm_call`，没有任何 E1-E4 evidence，reflection 报 `no root cause candidates` 但没有 repair action。
Subagent review 还指出 no-anomaly 后 downstream tool 只在事后失败，可能已经写入 E2/E3/E4 evidence；
episodic memory 在 run finish 前写入，也可能让未成功终结的 run 被未来读取。

### 决策

Reflection 在没有任何当前 evidence 时建议 `detect_anomaly` repair；repair prompt 要求 detect 为第一步，
若 E1 为 anomaly 才继续正常 RCA path。GuardMiddleware 在 guard-passed E1 表示 no anomaly 后，
对 drilldown、related signal、contribution、ranking 做 pre-handler hard reject，避免落 downstream evidence。
成功路径先持久化 terminal run status，再写 episodic/reflection memory。

### 理由

无 evidence 的 repair 应回到 RCA 起点，而不是静默失败或依赖 LLM 自行重试。No-anomaly 是硬边界，
必须在工具 handler 前拦截，不能等事后 reflection 才发现污染。Episodic memory 是未来规划输入，
只能来自已经 durably succeeded 的 run。

### 被否决的方案

- 让 C06 直接失败并靠下一轮 eval 重试：会把 LLM 无工具输出当成不可修复错误。
- 只在 orchestrator 事后检查 no-anomaly downstream trace：能阻止最终报告，但不能阻止已持久化 evidence。
- 在 memory write 后再 finish run：如果 finalization 失败，未来会读到未成功 run 的 memory。

### 后续跟进

用 fresh Predict-Then-Verify 轮次验证 C06/C07、C19/C20 和 memory paired cases；更新 predictions
把 memory influence 表述为“context present, final evidence influence forbidden”。

---

## ADL-0027: Eval case result 使用幂等 upsert 支持中断重试

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | MetricRepository, API eval routes, direct eval runner, HTTP eval client |

### 背景与场景

长时间 `eval-stream` 和 HTTP eval 可能在 case 已写入后中断或重跑同一个 `EVAL_ID`。
原 `eval_case_result` 只有 insert/idempotency 校验，若第二次运行的 detail 中 token、latency、
run id 等字段不同，会撞 `(eval_id, case_id)` 唯一键并报 `SYSTEM_TABLE_WRITE_FAILED`。

### 决策

保留 `create_eval_case_result` 供严格创建语义使用，新增 `upsert_eval_case_result`。
Direct eval runner 和 HTTP API 的 case-result endpoint 统一调用 upsert，SQLite 使用
`ON CONFLICT(eval_id, case_id)`，MySQL 使用 `ON DUPLICATE KEY UPDATE` 覆盖评分字段与 detail。

### 理由

eval progress 是可重放的观测结果，不是不可变审计事件；同一个 eval/case 的最新结果应覆盖旧进度。
这样可以从根本上降低重复 eval id 或中断恢复导致的系统表写失败，同时仍然 fail-fast 暴露真实数据库写入错误。

### 被否决的方案

- 每次失败后手动换新的 `EVAL_ID`：绕开了重复键问题，但没有解决恢复和 HTTP 客户端重试语义。
- 在 client 侧先查再决定 insert/update：会引入竞态，并把持久化规则分散到 API 客户端。
- 吞掉 duplicate key：会造成 summary 与 case result 不一致，违反 P8 observability 要求。

### 后续跟进

在最终 P8 eval 前复跑 full pytest、frontend tests，并用 fresh `EVAL_ID` 验证 summary 与 case result
增量写入。

---

## ADL-0026: Direct eval 增量 summary，memory discovery 使用最强候选并审计 memory read

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | evals/runner, discovery_policy, RunOrchestrator, MemoryRepository, eval scorer |

### 背景与场景

Predict-Then-Verify 的首轮 GPT-5 Nano `eval-stream` 在 memory-enabled C07 失败：
`fetch_related_signal` 选择了合法但非最强的 `affiliate`，随后 Adtributor/rank 将 `paid_ads`
提为 top1，造成 E3 与 top candidate 不一致并触发 `REFLECTION_REPAIR_FAILED`。同时 direct eval
在 memory pre-pass 失败时没有写任何 case artifact 或 progress summary，subagent review 还指出
memory required 配置、memory read 审计、reflection payload 与 pollution scoring 覆盖不足。

### 决策

无显式 filter 的 GMV `standard` / `channel_first` discovery 必须让首个 campaign E3 绑定
`E2_channel` 的最强候选，避免 memory 或 LLM 选择另一个合法 channel 后污染后续 rank 结构。
Direct eval 与 HTTP eval 一样使用 `eval_run.summary` upsert 写 `complete=false` 进度，每个 memory
case 和 baseline case 完成后更新 summary；baseline case 立即写 `eval_case_result`。RunOrchestrator
写 `memory_read` trace step 记录本 run 读到的 memory id/layer/key/confidence/source；reflection
memory payload 保存 verifier issues；`memory_required=true` 且 `memory_enabled=false` 在配置层失败。

### 理由

首个 E3 与最终 top candidate 的一致性必须由确定性 policy/guard 保证，不能靠 repair 或 LLM 自我纠偏。
Direct eval 进度持久化让长 eval 和 memory pre-pass 失败可观察，避免把静默等待误判为死循环。Memory
read 审计和 reflection issues 使 P8 memory influence 可追溯，但仍不把 memory payload 变成 evidence。
Pollution scoring 必须校验 current-run evidence id，不能只检查字符串里是否包含 `:E`。

### 被否决的方案

- 给 `signal_consistency` 增加宽松 repair 或忽略 E3/top1 不一致：会掩盖工具链结构缺陷。
- 禁用 episodic/reflection memory prompt：会绕过 P8 memory retrieval，而不是约束其影响边界。
- 只在最终 direct eval 写 summary：memory pre-pass 或早期 baseline 失败时仍不可观察。
- 只检查 memory id 前缀：cross-run evidence id 仍可能通过污染检查。

### 后续跟进

复跑 full pytest、frontend tests，并按 Predict-Then-Verify 写第二轮 predictions 后运行
`make eval-stream EVAL_ID=...` 与 `make eval-gaps`。

---

## ADL-0025: HTTP eval 通过 API 持久化结果并增量发布 summary

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-16 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | evals/client, api/routes, api/schemas, MetricRepository, eval artifacts |

### 背景与场景

P8 要求 HTTP eval 与 backend runner 解耦，并使用 API surface 验收 RCA 运行。但 HTTP eval 只写本地
artifact，没有写 `eval_run` / `eval_case_result`，且 CLI 在全部 case 完成前没有中间 summary，
长时间 GPT-5 Nano eval 容易被误判为死循环。

### 决策

HTTP eval client 继续只通过 HTTP API 与 backend 交互，不直接 import repository。API 新增
`POST /api/evals/{eval_id}/summary` 与 `POST /api/evals/{eval_id}/case-results`：summary endpoint
对 `eval_run.summary` 做显式 upsert，case-result endpoint 写 `eval_case_result`。HTTP eval 启动时
先写 progress summary；每完成 memory case 或 baseline case 都更新本地 artifact、写 progress summary，
每完成 baseline case 立即写一条 case result；最终 summary 覆盖 progress summary 并保留完整阈值结果。
CLI progress summary 输出到 stderr，最终 summary 仍输出到 stdout。

### 理由

这样保持了 ADL-0012/P8 的 API-only eval 边界，同时让 `/api/evals/{eval_id}` 在运行中可观察进度，
避免只有最终 artifact 才能看到 summary。case result 增量写入能在长 eval 中保留已完成 case 的证据，
summary upsert 是显式进度状态更新；任何 persistence API 失败都会 typed fail-fast，不被吞掉或降级。

### 被否决的方案

- 让 HTTP client 直接 import `MetricRepository`：会破坏 eval-backend decoupling，并把 API 验收路径变回
  本地 DB 混合路径。
- 只在本地 artifact 写增量 summary：不能满足 eval system table observability。
- 只在最终写 `eval_run` 和 `eval_case_result`：长 eval 期间不可观察，且中途失败时丢失已完成 case 评分。

### 后续跟进

Docker Desktop / MySQL 恢复后复跑 `make seed`、full pytest、HTTP eval，并确认 DB 中
`eval_run` / `eval_case_result` 对最新 HTTP eval 非空。

---

## ADL-0023: Reflection repair guard 支持证据驱动续步，eval 评分前验证持久化终态

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | GuardMiddleware, RunOrchestrator repair prompt, evals runner/client, memory observability |

### 背景与场景

GPT-5 Nano eval 的 C08 baseline leg 没有复现 `SYSTEM_TABLE_WRITE_FAILED`，但在 Reflection repair
中先按建议补 `fetch_related_signal` 生成 E3 后，正确的下一步 `calculate_contribution` 被
`required_repair_action=fetch_related_signal` 拦截，最终变成 `REFLECTION_REPAIR_FAILED`。子代理
review 同时指出 eval HTTP/direct scorer 仍可能在持久化 run 缺失或非终态时读取 artifacts 评分，
memory prompt 未展示 confidence，`/memory` 可能展示同 metric 的未来 memory。

### 决策

Repair guard 仍要求第一步严格执行 Reflection suggested action，但当 required action 是
`fetch_related_signal` 且当前 run 已有 guard-passed E3、尚无 E4 时，允许同一 repair turn 的
`calculate_contribution`；当 required action 是 `fetch_related_signal` 或 `calculate_contribution`
且当前 run 已有 guard-passed E4 时，允许 `rank_root_causes`。Direct eval 和 HTTP eval 在评分前
必须验证持久化 run 为 `succeeded` 或 `no_anomaly`；missing/running/failed 均 typed fail-fast，
failed run 保留原始 error_code。Memory prompt context 展示 confidence，`/memory` 只返回 run
自己写入的记录、run 开始前可读的 `metric_id|run` 记录，以及 run 开始前可读的 semantic memory。
memory observability 的读取必须先按 run/metric key 收窄，再做 payload/time 过滤；无关历史记录不能
通过固定 LIMIT 挤掉目标 run 可读记录。

### 理由

Reflection repair 的 suggested action 可能只补齐缺失链条的第一段；E3 成功后仍必须完成 E4/E_rank，
否则 C08 这类 AOV decomposition 无法通过反思。允许续步必须由当前 run 已持久化证据决定，不能把
repair turn 放宽成任意工具调用。Eval scorer 的输入必须是终态 persisted artifacts；否则系统/运行时
失败会被阈值或 scoring 结果掩盖。Memory observability 应反映 run 可用或自身产生的 memory，而不是
同 metric 的未来记录。
E4 的续步放行必须检查 `guard_status=passed`，不能把 failed/corrupt E4 当成 repair 已完成证据。

### 被否决的方案

- 移除 repair guard：会重新允许 repair turn 漂移到任意工具，违反 ADL-0009/0010 的结构化 repair。
- 把 `max_repair` 增大：只是给 LLM 多次尝试机会，不能解决 guard 阻断正确续步的根因。
- 让 eval scorer 继续处理 missing/running artifacts：会把基础设施失败伪装成业务指标失败。
- `/memory` 按 metric_id 全局扫描：会把其它 run 或未来 run 的 memory 误显示为当前 run 上下文。
- `/memory` 先取固定数量全表记录再过滤：会在长期 eval/生产历史累积后静默漏掉当前 run 可读 memory。

### 后续跟进

复跑 full pytest、frontend tests、GPT-5 Nano direct eval；在长 eval 期间继续用 subagent 预测与真实
结果偏差，重点观察 C08/C09 和 `SYSTEM_TABLE_WRITE_FAILED` 是否复现。

---

## ADL-0024: HTTP eval timeout 覆盖同步 RCA 运行窗口，仍保持 typed fail-fast

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | evals/client, Makefile |

### 背景与场景

`make eval-http` 通过 HTTP POST `/api/rca/runs` 触发同步 RCA。GPT-5 Nano 的部分 case 会超过
120 秒客户端默认 timeout；API 服务端随后仍能完成并返回 200，但 HTTP eval client 已先失败为
`EVAL_HTTP_REQUEST_TIMEOUT`。这不是系统表写入失败，也不是 scorer 输入错误，而是 eval 客户端等待窗口
短于真实同步 LLM run。

### 决策

HTTP eval request timeout 变成显式配置：CLI 支持 `--timeout`，Makefile 通过 `HTTP_TIMEOUT` 传入，
默认 600 秒。timeout 仍然是 typed fail-fast `EVAL_HTTP_REQUEST_TIMEOUT`，不纳入 LLM transient
case retry，不读取半成品 artifacts，不把失败降级为 threshold miss。

### 理由

HTTP eval 的职责是验证 API surface，而当前 API run endpoint 是同步运行模型与工具链。等待窗口应覆盖
合法同步运行耗时；否则会把一个仍会成功的 API run 误判成 transport failure。提高显式 timeout 不改变
业务逻辑、不引入 fallback，也不隐藏真正的超时：超过配置窗口仍会立即失败并报告 typed error。

### 被否决的方案

- 把 POST timeout 作为 LLM transient 自动重试：server 端原请求可能仍在运行，会制造重复 run 并掩盖
  HTTP transport 边界。
- 改成忽略 timeout 后轮询同一个 run：client timeout 时通常拿不到 run_id，无法可靠绑定原 run。
- 降低 eval scope 或只跑少量 HTTP cases：不能验证 P8 HTTP eval decoupling。

### 后续跟进

复跑 `make eval-http BASE_URL=http://127.0.0.1:8000 PROVIDER=openai MODEL=gpt-5-nano`，确认 API 路径
在默认 600 秒 request timeout 下完成 20-case paired scoring。

---

## ADL-0022: Eval 不评分 typed run failure，memory_record 复用系统表幂等写边界

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | evals/runner, memory_repo, RunOrchestrator, MetricRepository |

### 背景与场景

子代理 review 发现 `SYSTEM_TABLE_WRITE_FAILED` 的根因修复仍有两个架构边界漏洞：direct eval 会把
非 transient 的 failed run 继续交给 persisted-artifact scorer，最后只暴露
`EVAL_THRESHOLD_NOT_MET`；`MemoryRepository.write` 直接写 `memory_record`，没有复用
`MetricRepository` 的幂等 retry/ambiguous commit 确认。

### 决策

Direct eval 的 `_run_case_with_retries` 只有在 RCA result 没有 `error_code` 时才返回 run_id 并进入
artifact scorer，且成功状态必须是 `succeeded` 或 `no_anomaly`；typed LLM transient 仍按同 case retry 预算重试，重试耗尽或任何非 transient
typed failure 都直接抛原始 `EvalRuntimeError(code=error_code)`。生产默认的
`MemoryRepository` 由 orchestrator 注入当前 `MetricRepository.create_memory_record`，使
`memory_record` 与 trace/evidence/sql_audit/eval rows 共用同一套系统表幂等写边界；低层 memory
unit tests 仍可不注入 writer 以验证读写规则。项目 typed uppercase error 前缀优先于 provider
transient 文本分类，避免 `SYSTEM_TABLE_WRITE_FAILED: ... timeout ...` 被重标为可重试的
`REQUEST_TIMEOUT`；显式传入不兼容的 `system_repository` 必须 fail-fast，不允许静默退回直接写。
`run_rca` 对自己创建的 `MemoryRepository` 负责 close，避免长 eval 的 memory-enabled leg 累积
独立 MySQL engine/pool；调用方显式注入的 dependencies/memory_repo 仍由调用方管理生命周期。
`finish_agent_run` 这种 terminal UPDATE 也声明幂等读回条件；transient/duplicate ambiguous write
后若 agent_run 已经持久化为目标 status/error/tokens，则确认成功，不再把已成功的 RCA 覆盖为 failed。

### 理由

Eval scorer 只能评估成功落库的 RCA artifacts，不能把系统写失败降级成业务阈值失败。memory 是 P8
核心写路径，若绕开统一 repository retry，就会把同类连接 transient 重新暴露为
`MEMORY_WRITE_FAILED`，并削弱 `SYSTEM_TABLE_WRITE_FAILED` 根因修复的一致性。

### 被否决的方案

- 继续让 scorer 读取 failed run artifacts：会隐藏 primary error code，导致 eval 反馈不可诊断。
- 只看 `error_code` 是否为空来判定 eval attempt 成功：malformed/failed result 会被 scorer 吸收。
- 在 eval runner 里对 `SYSTEM_TABLE_WRITE_FAILED` 做整案重跑：会掩盖 schema/payload 错误。
- 在 `MemoryRepository` 里复制一套 retry 逻辑：会产生第二个系统表写策略，长期更难审计。
- 把带 `timeout` 文本的 typed 系统错误归类成 LLM transient：会让 eval 层错误重试边界失真。
- 长 eval 后只增加系统写 retry budget：不能解决每个 memory run 创建独立 engine 后未释放的资源压力。
- 只给 INSERT 做 ambiguous commit 确认：terminal UPDATE 同样可能已提交但客户端收到 lost connection。

### 后续跟进

复跑 full pytest、frontend tests、GPT-5 Nano direct eval 与 HTTP eval，确认失败边界和 memory 写入
均不再出现 P0 fallback/shortcut。

---

## ADL-0021: GMV 标准发现优先验证 channel/campaign，SQL audit 用 audit_key 幂等重试

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | DiscoveryPolicy, GuardMiddleware, MetricRepository, schema.sql, seed_data |

### 背景与场景

最终 eval 反馈显示两类残余风险：C06 memory leg 在标准 unscoped GMV 问题上，可能受历史
stockout episodic 与强 E2 category 信号影响，先验证 category/inventory 并选出 stockout；
C09 memory leg 已有完整 E_rank，但 finalization 前后仍可能遇到 `SYSTEM_TABLE_WRITE_FAILED`。
ADL-0020 禁止 `sql_audit` retry 虽然保守，但会让 audit 写入 transient 直接污染 eval。

### 决策

标准 unscoped GMV discovery policy 仍要求先完成 `E2_channel`、`E2_category`、`E2_product`，
但首个 E3/E4 必须验证 `dimension=channel` + `signal_type=campaign`，不强制 channel top element。
`sql_audit` 增加 `audit_key` 唯一键；每次 audit 写入生成一次稳定 key，repository retry 复用该
key，ambiguous commit 后通过读回 payload 匹配来确认已提交。

### 理由

C06 是多渠道 campaign 发现场景；标准 GMV discovery 若允许首个 E3 任意选择 category/product，
LLM 会在当前证据完成前过早固化局部库存解释。把首个相关信号约束为 channel/campaign 是结构化
policy，不依赖 prompt keyword。`sql_audit` 仍保持每次 SQL 执行一条审计；`audit_key` 只用于同一次
write 的幂等 retry，不合并不同 SQL 执行。

### 被否决的方案

- 在 memory prompt 中隐藏所有 episodic memory：会绕开 P8 memory retrieval，而不是解决 discovery policy。
- 继续禁止 `sql_audit` retry：会把连接级 transient 暴露成 RCA case 失败。
- 用 `(run_id, sql_hash)` 作为 audit 幂等键：会错误合并同一 run 中合法重复执行的同一 SQL。

### 后续跟进

复跑 GPT-5 Nano eval，确认 C06 memory top1 不低于 baseline 且 `SYSTEM_TABLE_WRITE_FAILED`
不再出现在完整 evidence 后的 finalization 路径。

---

## ADL-0020: Reflection repair 使用 JSON contract，系统写入 retry 必须幂等

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | superseded by ADL-0021 |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | RunOrchestrator, GuardMiddleware, MetricRepository |

### 背景与场景

P8 live eval 中 `SYSTEM_TABLE_WRITE_FAILED` 前的失败 run 已经产出完整 E1/E2/E3/E4/E_rank，
但 repair turn 又连续调用 `rank_root_causes`，且 `target_date` 被写成
`datetime.date(2026, 6, 5)`。根因不是 scorer 或业务归因，而是 repair instruction 把
`suggested_action.args` 用 Python dict repr 拼入 prompt，破坏了工具 JSON schema。复核还发现
runner-level final token retry 会为每次重试生成新的 `step_id`，而非幂等 `sql_audit`/旧
`eval_case_result` insert 在 ambiguous commit 后重试可能重复写入。

### 决策

Reflection repair payload 与 exact tool args 一律通过 JSON-safe serialization 下发，date/datetime
值转成 ISO 字符串。系统表 retry 只存在于 `MetricRepository` 写入边界：INSERT 必须声明稳定
幂等键，ambiguous commit 后若遇到 duplicate key，必须读回已提交行并确认 payload 匹配才算完成。
`sql_audit` 不具备稳定幂等键，因此不做 retry；`eval_case_result` 增加 `(eval_id, case_id)`
唯一键后可幂等确认。transient 判定覆盖 SQLAlchemy `InterfaceError`/`InternalError` 中无 errno
或 errno=0 的连接层错误；重复键 payload 不匹配等非 transient 写失败仍 fail-fast 为
`SYSTEM_TABLE_WRITE_FAILED`。

### 理由

Repair turn 是工具调用 contract，不能依赖模型从 Python repr 推断 JSON。先消除无效工具调用循环，
再在 repository 写入边界吸收连接层 transient，能够减少额外 trace 写入压力。幂等确认防止
ambiguous commit 被二次写入放大，同时不放宽非 transient schema/payload 错误。

### 被否决的方案

- 在 eval 层重跑 `SYSTEM_TABLE_WRITE_FAILED`：会掩盖 repair prompt contract 破损。
- 在 middleware 中把 `datetime.date(...)` 自动改写成 ISO：这会把非法工具参数变成隐式 fallback。
- 对所有 SQLAlchemyError 无条件重试：会延迟并掩盖重复键、列长度、JSON payload 等确定性错误。
- 在 runner 层重试 final token trace：会用新 `step_id` 写出重复 observability rows。
- 对 `sql_audit` 这类非幂等 auto-increment insert 做 retry：ambiguous commit 后无法证明是否重复。

### 后续跟进

继续用 live eval 验证 repair loop 是否消失；如再次出现系统写失败，优先比对最后一条 trace、
幂等确认结果与底层 SQLAlchemy 错误类别。

---

## ADL-0019: 系统表写失败在写入边界解决，eval 不做整案重跑

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-15 |
| 状态 | accepted |
| 关联迭代 | P8 memory observability eval decoupling |
| 影响范围 | GuardMiddleware, TraceWriter, RunOrchestrator, evals/runner.py, evals/client.py, Makefile |

### 背景与场景

P8 paired eval 复现了 P7 里的 `SYSTEM_TABLE_WRITE_FAILED`。排查发现失败 run 已有完整
tool trace/evidence，final token trace 也能写入；后续 dry-run operation_task 与 memory_record
均可写入。剩余高风险入口是模型幻觉的未注册 tool name 直接写入 `trace_step.action VARCHAR(48)`，
以及本地 eval 受 `LANGSMITH_TRACING=true` 外部上报失败污染。后续复跑还暴露出另一类不相关
问题：provider rate/timeout/5xx 在 agent invoke 边界被折叠为非重试的 `AGENT_INVOKE_FAILED`，
导致 eval 无法区分真实编排 bug 与 typed LLM transient。

### 决策

模型 tool-call 边界写入 `trace_step.action` 时只保存注册工具名或固定 `invalid_tool_call`；未注册/超长 tool name 仍返回
`ACTION_SCHEMA_INVALID`，但不再把 untrusted model 字符串写入受限 action 列。token usage 只持久化
prompt/completion/total 三个稳定字段。`make api`、`make eval`、`make eval-http` 显式关闭
LangSmith 外部 tracing。Direct eval 与 HTTP eval 不再对 `SYSTEM_TABLE_WRITE_FAILED` 做整 case retry；系统表
写入的 transient retry 保留在 repository 写入边界，并覆盖 invalidated DBAPI connection、
connection-loss/packet-sequence 等连接级写入抖动；ADL-0020 进一步要求 INSERT retry 必须幂等确认。
`RunOrchestrator` 在捕获 provider
异常时将 429、timeout、connection、5xx/server_error 显式映射到 `RATE_LIMIT_EXCEEDED`、
`REQUEST_TIMEOUT` 或 `LLM_REQUIRED_UNAVAILABLE`；未知 invoke 错误仍保留为
`AGENT_INVOKE_FAILED`。Direct eval 与 HTTP eval 只对这些 typed LLM transient 做有界同 case retry。

### 理由

系统表写失败是持久化契约问题，应在写入边界和 schema-safe trace 表达上解决。Eval 层重跑整案会
掩盖非 transient schema/payload 错误，并产生重复 partial runs。关闭本地外部 tracing 不影响项目
内部 persisted observability，可避免网络上报失败改变验收结果。
Typed provider transient 保持 fail-fast：run 仍失败并落明确 error_code；eval 只对这些
明确 transient 做有界重试，不会把 schema/action/persistence bug 伪装成可重试抖动。

### 被否决的方案

- 继续把 `SYSTEM_TABLE_WRITE_FAILED` 当作 eval transient 重跑：会掩盖 schema/action 长度问题。
- 扩大 DB column 或无条件截断 action：放大 schema 而不约束 untrusted model 输出。
- 忽略 trace 写失败并把 run 标成功：违反 fail-fast observability contract。
- 将所有 `AGENT_INVOKE_FAILED` 都纳入 eval retry：会掩盖真实编排 bug。

### 后续跟进

如再次出现系统表写失败，优先查看 `TraceWriteError.message` 中保留的底层 SQLAlchemy/MySQL 错误，
再决定是否需要 schema 迁移或更细 typed error。

---

## ADL-0018: Eval runner 对 typed transient case failure 做有界重试

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | superseded by ADL-0019 |
| 关联迭代 | P7 eval acceptance hardening |
| 影响范围 | evals/runner.py, MetricRCA §17, final-design/03 |

### 背景与场景

P7 live eval 中 C08 已产出正确 E1/E2/E3/E4/E_rank，selected candidate 为
`product=2` / `aov_drop`，但在并发 20-case run 中 E_rank 之后的系统表写入偶发返回
`SYSTEM_TABLE_WRITE_FAILED`，导致该 case 的 RCA 业务证据正确但 run status 标失败。
单跑 C08 以及 C08+C09 并发复现均能成功，说明问题属于 case 级基础设施瞬态失败，而不是
scorer、prompt、seed 或 ground truth 问题。

### 决策

Eval runner 的 case retry predicate 从 LLM transient 扩展为 typed eval transient：
`LLM_REQUIRED_UNAVAILABLE`、rate/timeout，以及 `SYSTEM_TABLE_WRITE_FAILED`。retry 仍然是
同一 case 的有界 attempt（`eval_llm_max_attempts` 默认 3 且配置必须 ≥1）；新的 attempt
使用带后缀的唯一 run_id，`eval_attempts` 写入 case detail。最终 scoring 只读取最后一个
attempt 的 persisted artifacts；若 attempt 耗尽，case 仍失败并进入真实 summary。

Production `RunOrchestrator` 不吞掉系统写失败：final token trace 或任务/记忆写失败仍使该
run fail-fast。这个 retry 只存在于 eval 调度层，用于吸收已诊断的基础设施瞬态抖动。

### 理由

Eval 的目标是评估 RCA 系统在自然问题上的能力，而不是让一次偶发系统表写入耗尽污染 20-case
结果。重试必须 typed、bounded、可审计，并且不能凭空构造成功 artifacts。保留失败 attempt
的 agent_run/trace，同时只按成功 attempt 的持久化产物计分，能兼顾可追溯与验收稳定性。

### 被否决的方案

- 忽略 final token trace 写失败并把生产 run 标成功：违反 fail-fast 和 observability contract。
- 手工重跑整轮 eval 直到绿色：不可审计，且掩盖 flaky case。
- 修改 C08 question/ground truth 或放宽 anomaly/report threshold：违反 eval integrity。

---

## ADL-0017: Reflection repair action guard 与 E3 signal root-cause 覆盖

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P7 eval acceptance hardening |
| 影响范围 | RunOrchestrator, GuardMiddleware, calculate_contribution |

### 背景与场景

P7 live eval 中 C07 已有完整 E1/E2/E3/E4，但 Reflection repair 重入后模型重新执行
detect/drilldown，耗尽 step budget，未调用 `rank_root_causes` 产出 E_rank。C13 的 E3
为 `refund_quality`/`complaint_rate` 异常，但 E4/E_rank 沿用 E2 product drilldown 的
`stockout` root_cause_type，导致 top1 root cause type 与 ground truth 不一致。

### 决策

Reflection repair 重入必须携带 `suggested_action.action` 作为
`RunGuardContext.required_repair_action`。repair turn 中 middleware 只允许该 tool，其它
tool 返回 recoverable `ACTION_SCHEMA_INVALID` 且不消耗预算；orchestrator repair message
也明确要求只调用该 tool，携带 exact suggested JSON args，并禁止文本回答。Reflection repair
args 从 parsed scope 或 persisted E1 summary 继承 filters。`calculate_contribution` 在生成 E4 selected candidate 时读取匹配
E3 summary 的 `signal_type`/`signal_metric_id`，用 `refund_quality`、`campaign`、
`conversion`、`inventory` 映射 root_cause_type；GMV AOV decomposition 仍可在其后覆盖为
`aov_drop`。

### 理由

repair 的下一步已经由 deterministic Reflection 给出，允许模型在 repair turn 自由重跑前序
工具会烧预算并掩盖真正缺口。E4 candidate 的根因类型应由当前 run 的 related signal evidence
校正；否则 `refund_quality` 证据只提高 signal_severity，却不能改变从 E2 delta 继承来的粗粒度
root cause。

### 被否决的方案

- 让 Reflection 直接执行 `rank_root_causes`：绕过 deepagents/middleware tool-call path。
- 仅靠 prompt 要求模型 repair 时别重跑前序工具：C07 已证明不稳定。
- 根据 eval ground truth 覆盖 root_cause_type：违反 eval integrity。

---

## ADL-0016: channel-first 与 organic-first discovery 使用结构化 first-signal policy

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P7 eval acceptance hardening |
| 影响范围 | ParsedIntent, DiscoveryPolicy, GuardMiddleware, calculate_contribution, seed data |

### 背景与场景

P7 20-case eval 的 C09 是无显式 slice 的「stable merchandising」GMV 问题，ground
truth 为 organic channel traffic drop。先前 `channel_first` policy 同时要求
`dimension=channel`、`signal_type=campaign` 和 `E2_channel` top candidate，导致
agent 必须选择 paid_ads，即使 organic 的 related traffic/campaign signal 更能解释该
case。该约束也让 `calculate_contribution` 拒绝非 top 但已有 E3 证据支持的 channel。

### 决策

`channel_first` 只强制首个 E3/E4 chain 使用 `dimension=channel` 与
`signal_type=campaign`；不强制 channel top element。为表达 C09 的「stable
merchandising, organic traffic/campaign first」语义，`ParsedIntent.analysis_strategy`
新增 `organic_first`，orchestrator 将其转换为 `DiscoveryPolicy(first_signal_dimension=channel,
first_signal_type=campaign, first_signal_element=organic)`。middleware 只执行这些结构化
policy 字段，不读取或关键词匹配 raw question。`product_first` 仍强制 `E2_product` top
candidate，以保护 merchandise/price/AOV 场景。E4 contribution 允许 selected element 是
attribution candidates 中的非第一名，但必须存在于候选列表并由匹配 E3 证据支持；selected
candidate 的 `signal_severity` 可由 E3 的 `delta_pct` 提升。C09 seed 中 organic 的
campaign/UV signal strength 调整为 strongest channel drop，使 persisted evidence 与
ground truth 一致。

### 理由

Channel discovery 的目标是找到被 related signal 证明的 traffic/campaign 机制，不是把
GMV 贡献第一名硬编码成根因。Top-element 强制适合 product-first/AOV，因为 E4 factor
decomposition 需要验证最强 product slice；普通 channel-first 由 E3 signal evidence 与
ranker 共同决定。需要业务语义指定 element 时，必须通过 intent planner 的结构化
strategy 和 `DiscoveryPolicy.first_signal_element` 表达，而不是在 middleware 中复制自然语言
keyword parser。

### 被否决的方案

- 修改 eval question 或 ground truth：违反 eval integrity。
- 在 middleware 根据 raw question 识别 C09：违反 ADL-0013。
- 放开不存在于 attribution candidates 的任意 element：会允许无贡献切片进入 E4。

### 后续跟进

- P8 如引入独立 traffic signal type，可将 organic traffic evidence 从 campaign signal
  中拆出，但仍应保持 guard 消费结构化 policy 而非 question keyword parser。

---

## ADL-0015: final token trace 写入只允许 bounded retry，禁止静默丢弃

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | superseded by ADL-0020 |
| 关联迭代 | P7 eval acceptance hardening |
| 影响范围 | RunOrchestrator, TraceWriter, eval acceptance |

### 背景与场景

20-case live eval 中出现过完整 E1/E2/E3/E4/E_rank 已持久化、但 agent loop 结束后
pending token usage trace 写入返回 `SYSTEM_TABLE_WRITE_FAILED`，导致 run 被标 failed。
失败 trace 后续可用同 schema/payload 在 rollback 事务中写入，说明不是确定性 schema 或
JSON payload 错误，而是 finalization 阶段的系统表 transient。

### 决策

`RunOrchestrator` 在 flush final pending token usage 时，对 final `llm_call`
trace_step 的 `SYSTEM_TABLE_WRITE_FAILED` 做小次数 bounded retry。retry 只覆盖这类
observability finalization 写入；重试耗尽仍然让 run failed，不把缺失 token trace 的 run
伪装成成功。

### 理由

token trace 是 mandatory observability，不能静默丢弃；同时真实 LLM eval 不应因为
terminal evidence 已完整后的瞬时 trace 写入抖动而直接损坏结果。bounded retry 保持
fail-fast 边界：不替换数据、不改 evidence、不跳过 trace，只对同一 typed write 再试。

### 被否决的方案

- 忽略 final token trace 写入失败并继续 succeeded：违反 trace persistence 与 no-fallback。
- 把所有 `SYSTEM_TABLE_WRITE_FAILED` 在 eval runner 层当作可重跑 case：可能掩盖
  非 transient 的 schema/duplicate-key 错误。
- 扩大 repository 对非 transient errno 的 retry：会削弱系统表写入的 fail-fast 契约。

### 后续跟进

- 如再次出现系统表写入抖动，应优先记录原始 MySQL errno/SQLSTATE 到 typed error
  detail，而不是扩大 retry 范围。

---

## ADL-0014: P7 eval 并发下的 LLM parse retry 与 bounded repository pools

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P7 eval acceptance hardening |
| 影响范围 | LLMIntentPlanner, MetricRepository, eval runner concurrency |

### 背景与场景

GPT-5 Nano 在 20-case eval 并发运行时会偶发对已支持自然问句返回
`PARSE_FAILED` 或 malformed structured output；同时每个 eval worker 拥有独立
repository/trace writer，默认 SQLAlchemy pool 会放大 MySQL 连接和系统表写入压力。

### 决策

Intent planner 对 malformed schema 或 `PARSE_FAILED` 做最多 3 次同模型、同 schema、
同 metadata 的有界 retry；`METRIC_NOT_FOUND`、`DIMENSION_NOT_ALLOWED`、
`DATE_RANGE_INVALID` 等 typed semantic errors 不重试。Repository from Settings 使用
小型有界连接池，并将 SQLAlchemy pool timeout 与明确 MySQL transient errno 纳入有界
system-table write retry。

### 理由

这不是 fallback：不替换 provider/model，不用关键词 parser，不默认补 intent。retry 只在
同一个 LLM contract 未能稳定产出结构化结果时重试，最终仍 typed fail-fast。小型连接池
让 case 级并发保持隔离，同时避免 worker 数量乘以默认 pool 大小压垮本地 MySQL。

### 被否决的方案

- 把 PARSE_FAILED 当作成功并硬编码 intent：违反 LLM-first 与 no-fallback。
- 在 middleware 中恢复 question keyword parser：违反 ADL-0013。
- 共享一个全局 repository/engine 给所有 worker：会重新引入并发可变状态污染。

### 后续跟进

- P8 eval HTTP client 应保留 case-level concurrency，但继续保持 per-case repository
  或 request isolation。

---

## ADL-0013: discovery guard policy 由 ParsedIntent 驱动，middleware 禁止解析 question 文本

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P7 acceptance hardening |
| 影响范围 | Intent planner, RunOrchestrator, GuardMiddleware, Reflection alias helpers |

### 背景与场景

P7 eval 修复中曾在 `GuardMiddleware` 内加入 question 关键词判断，用于区分 broad
GMV channel-first 与 merchandise/AOV product-first 路径。这让基础 guard 层耦合到
自然语言措辞，且英文关键词变化或中文问题会绕过策略；同时 E3→E2 alias mapping 在
middleware/reflection 两处重复。

### 决策

`parse_question` 的结构化输出新增 `analysis_strategy`：
`standard`、`channel_first`、`product_first`。LLM intent planner 负责把自然语言问题
解析为该字段。`RunOrchestrator` 将 `ParsedIntent` 转为 `DiscoveryPolicy` 并注入
`GuardMiddleware`。RunOrchestrator 的 explicit guard scope 也只能来自
`ParsedIntent.filters` 或 `ParsedIntent.dimension/element`，不得用 regex/keyword 从
raw question 文本补猜。middleware 只消费结构化 policy，不读取或关键词匹配原始
question 文本。E3 维度 token 与 E3→E2 alias 映射集中到
`agent/evidence_aliases.py`，供 producer、middleware、reflection 共享。

### 理由

自然语言理解属于 intent planner 的职责；guard 层的职责是执行结构化约束并产生
typed rejection。这样既保留 P7 eval 所需的 channel-first/product-first 强约束，又避免
在 middleware 中复制 keyword parser 或业务语义判断。

### 被否决的方案

- 继续在 middleware 里扩充关键词列表：不可维护，且不支持多语言措辞。
- 只靠 prompt 要求 agent 先走某一路径：违反 guard 必须可验证的要求。
- 让每个 guard 方法自行判断 metric/question family：会继续扩大基础设施层业务耦合。

### 后续跟进

- P8 若扩展更多 discovery 策略，应先扩展 `ParsedIntent.analysis_strategy` 或
  `DiscoveryPolicy`，再由 middleware 执行结构化 policy。

---

## ADL-0012: eval 解耦为 HTTP 客户端 + per-request LLM 选择 + GPT-5 Nano 验收策略

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P8 eval-backend decoupling（合并原 P8 memory + observability） |
| 影响范围 | API RunCreateRequest, eval runner 架构, Settings model floor, phase plan |

### 背景与场景

P7 迭代暴露 eval 架构的两个结构性缺陷：(1) eval runner 直接 `from metric_rca.agent.runner import run_rca`，完全绕过 API 层——FastAPI 路由/schema 校验/错误映射从未被 eval 真正测过；(2) LLM provider/model 是进程级配置（环境变量），不支持同一后端实例在不同请求中使用不同模型。用户需要同时提交 OpenAI (GPT-5 Nano) 和 DeepSeek eval 并对比结果。

另外，ADL-0009 的模型下限 (≥gpt-4.1) 用黑名单 `gpt-4.1-mini` 实现，过于脆弱——GPT-5 Nano 是 GPT-5 家族模型，指令遵循远强于 4.1-mini，应当被允许。

### 决策

#### D1: eval 解耦为 HTTP 客户端（P8 范围）

eval runner 拆分两层：
- **eval server-side**（保留现有 `POST /api/evals/run`）：仍可在进程内直调，用于 CI 和简单场景。
- **eval client**（新 `metric_rca/evals/client.py`）：纯 HTTP 客户端，逐 case 发 `POST /api/rca/runs`，通过现有 `GET /runs/{id}/evidence` 等端点读 persisted artifacts，本地评分。ground truth 内嵌 `cases.jsonl`（每行加 `expected_*` 字段），eval client 不需 DB 连接。

`make eval` 默认仍走直调模式（零配置）；`make eval-http BASE_URL=http://localhost:8000` 走 HTTP 客户端模式。

#### D2: per-request LLM provider/model

`RunCreateRequest` 新增可选字段 `llm_provider`/`llm_model`/`llm_api_key`。传入时覆盖 Settings 默认值，作用域仅限该 run。未传则沿用环境变量。这允许同一后端实例在不同请求中用不同模型。

#### D3: 模型门槛策略

删除 `_validate_eval_model` 中的 `gpt-4.1-mini` 硬编码黑名单。改为：
- eval summary 必须记录 `provider + model`（已实现）。
- 验收审查时人工判断模型能力是否足够（从 eval 结果倒推）。
- GPT-5 家族（含 Nano）、GPT-4.1（非 mini）、DeepSeek-V3 均为可接受的验收模型。
- 若 intent_accuracy < 1.0 且模型为已知弱模型，审查可要求升级模型重跑。

### 理由

eval 测的是"自然问句 → RCA"全链路，理应走 API 层。per-request 模型让对比实验成为配置问题而非部署问题。模型黑名单维护成本高于收益——eval 结果本身就是模型能力的最终判定。

### 被否决的方案

- 为每个 provider 部署独立后端实例：运维复杂度过高。
- eval 始终走 HTTP（删除直调模式）：增加 CI 复杂度，seed/test 不需要跑真 LLM。
- 保留 `gpt-4.1-mini` 黑名单并追加更多弱模型：无穷列表问题。

### 后续跟进

- P8 实现 eval client + per-request LLM 覆盖。
- 考虑 cases.jsonl 支持 per-case `llm_model` 字段（允许单 case 指定模型）。

---

## ADL-0011: LLM provider 通过 OpenAI-compatible 配置适配，禁止跨 provider key 替换

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | P7 provider compatibility hardening |
| 影响范围 | Settings, LLMIntentPlanner, deepagents factory, eval/smoke configuration |

### 背景与场景

P7 验收后需要切换到 DeepSeek 模型测试。DeepSeek 暴露 OpenAI-compatible chat
endpoint，但当前实现把 intent planner 固定为 `provider="openai"` +
`with_structured_output(..., method="json_schema")`，agent factory 也只在
`openai:` 前缀下构造 `ChatOpenAI`。真实 smoke 证明 DeepSeek endpoint 不支持
OpenAI `json_schema` response_format，而支持 JSON object/json_mode 类路径。

### 决策

新增统一 LLM 客户端构造边界：`provider/model/api_key/base_url` 全部来自 Settings。
`provider="openai"` 走原生 OpenAI；`provider="openai-compatible"` 或显式
兼容 provider（如 `deepseek`）必须配置 `llm_base_url`，否则 typed fail-fast。
intent planner 的 structured output method 也通过
`llm_structured_output_method` 显式配置（默认 `json_schema`；兼容 endpoint 可设
`json_mode`）。`MetricService` 和 deepagents factory 共享同一构造器，避免两套
provider 分支。

### 理由

模型/endpoint 切换应是配置问题，不能靠改底层实现或在业务代码写模型名特判。
同时，兼容 provider 不得静默读取 `OPENAI_API_KEY` 当成第三方 key；除原生 OpenAI
外，API key 必须通过 `METRIC_RCA_LLM_API_KEY` 等显式配置注入。缺少 key/base_url
是配置错误，应 fail-fast，而不是替换 provider 或回退模型。

### 被否决的方案

- 让 DeepSeek 伪装成 `provider="openai"` 且只改环境变量：会掩盖 provider 契约。
- 运行时捕获 `json_schema` 失败后自动改用 `json_mode`：这是 provider fallback。
- 为 DeepSeek 写专用业务分支：后续其他 OpenAI-compatible endpoint 仍需改代码。

### 后续跟进

无。

## ADL-0010: P7 终态证据链优先于最终 LLM 文本，且 evidence_id 必须预留别名长度

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-14 |
| 状态 | accepted |
| 关联迭代 | final-design P7（Adtributor + 20 case acceptance） |
| 影响范围 | RunOrchestrator, GuardMiddleware, eval runner, E2/E3 evidence aliases, prompts |

### 背景与场景

P7 真实 20-case eval 暴露两个非业务逻辑失败：长 case_id 生成的 run_id 加上
`E3_category_electronics` 超过 `evidence.evidence_id VARCHAR(64)`；另有真实 LLM
在 E4/E_rank 已持久化后，为最终文本收束再次调用模型时 hit rate limit，导致完整
证据链 run 被错误标 failed。

### 决策

eval run_id 最大长度收敛到可容纳 E3-family alias 的范围；E3 alias 采用紧凑维度 token
（如 `E3_ch_paid_ads`、`E3_cat_electronics`）并对过长元素 token 做确定性哈希截断。
GuardMiddleware 在 E4 前发现已有 E3-family evidence 时，拒绝额外
`fetch_related_signal` 并提示直接 `calculate_contribution`，不消耗预算。RunOrchestrator
仅在 transient LLM 错误且已存在 no_anomaly E1 或完整 E4+E_rank 终态证据链时继续
deterministic Reflection/report；未完成证据仍 fail-fast。

### 理由

schema 长度是持久化契约，不能靠数据库异常暴露给 agent；P7 多元素/跨维证明来自
drilldown Evidence + ranker-internal Adtributor，不需要逐元素 E3。最终报告已按
ADL-0006 从 persisted artifacts 投影，E_rank 后的 LLM 最终文本不是事实来源；允许
terminal artifact 继续可以消除速率抖动，同时仍由 Reflection 约束证据完整性。

### 被否决的方案

- 扩大预算或让 agent 多 fetch：会把 P7 证明路径从 Adtributor/E2 退回逐元素信号试探。
- 放宽 DB schema 或吞掉 `SYSTEM_TABLE_WRITE_FAILED`：掩盖 evidence_id 契约问题。
- 任意 LLM 错误后都继续投影：会变成 fallback；必须只允许已完成终态证据链。

### 后续跟进

无。

## ADL-0009: P7 修正——eval 题面零答案泄漏、Adtributor 归位确定性 ranker、多维须证明

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-13 |
| 状态 | accepted |
| 关联迭代 | final-design P7（Adtributor + 20 case） |
| 影响范围 | evals/cases、intent/expert prompts、rank_root_causes、adtributor_service、GuardMiddleware、seed、Settings/Makefile |

### 背景与场景

P7 首次用真实 LLM 跑 20-case eval，暴露：LLM 被业务词（stockout/UV/refund/AOV）
带偏 target metric；adtributor_attribute 作为独立 LLM 工具导致「调完就停在 E_adt」；
C06/C07 多维 case 难稳定。Codex 的应急修法部分越界：把 `metric_id=`、维度值、根因
机制写进 eval question（架空 intent_accuracy 与归因），并拟把 C07 真值塌缩成单维。
用户叫停，要求从架构师视角取「做对」的路径。

### 决策

1. **eval 题面完整性铁律**：问题是自然业务问句，**不得编码答案**——禁 `metric_id=`
   字面、禁根因机制词（from stockout / because refunds…）、发现型 case 禁止题面给出
   待发现维度/元素。intent-parse accuracy 保持真实可测。详见 final-design 02 §9.1。
2. **指标漂移正确修法**：intent/expert prompt 显式区分 target metric（被解释的 KPI）
   vs cause mechanism（待验证假设），替代把答案写进题面；并设 eval 模型下限
   （≥ gpt-4.1 同级，不接受 gpt-4.1-mini）。
3. **Adtributor 归位**：不引入 adtributor_attribute 工具；Adtributor 落在确定性
   `rank_root_causes` 内部按需调用（设计原意「仅用于排序」）。消除 E_adt 停滞失败类。
4. **run 级 target-metric 不变量守卫**：锚定 parsed intent，后续工具换 metric →
   recoverable METRIC_SCOPE_VIOLATION，防跨指标 evidence 污染。
5. **工具↔schema 单一真相源**：schema map 从工具注册表派生 + 覆盖测试。
6. **C07 多维必须被证明**：注入主导交叉，断言 dimension_elements 双维；不得塌缩单维。
7. seed 数据修复（保留少量订单避免 NULL、complaint baseline 拉低）属合法数据生成
   修复，非 runtime 特判 eval。

### 理由

eval 一旦把答案写进输入，20/20 测的就不再是「NL→RCA」能力，是隐蔽的 special-case。
Adtributor 是确定性排序，本就不该进 LLM 动作空间。守卫补丁不应沦为弱模型的拐杖。

### 被否决的方案

- 题面 `metric_id=` + 强制（架空 intent_accuracy）。
- C07 真值塌缩单维（掩盖多维能力缺口）。
- 仅靠 prompt 强化让 LLM「记得」E_adt 后继续（最脆弱）。

### 后续跟进

- 改写 prompt 08 / 新增 fix-003 承接以上；P7 应从已合并 P6 head 切独立分支
  `codex/p7-adtributor-20cases`，不叠在 P6 未提交工作树上。
- 保留 Codex 已做的合法成果：AdtributorService 纯服务、RootCauseCandidate v2 字段、
  seed 数据修复、v2 canonical 比较归一化、schema 漏注册修复。

## ADL-0008: 最终版编排层迁移到 deepagents，守卫语义移交 middleware + orchestrator

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-12 |
| 状态 | accepted |
| 关联迭代 | final-design（P6–P9，1 个月最终版） |
| 影响范围 | metric_rca/agent/ 全部；docs/MetricRCA.md §5/§6；COMPLIANCE_MATRIX 图结构行；约 1/3 测试 |

### 背景与场景

MVP（P1–P5）以手写 LangGraph StateGraph + 确定性主策略完成。1 个月最终版要求
验证 LLM-first 规划能力，用户在评审中明确选择破坏性重构到 deepagents，并纳入
开关式 Multi-Agent（分诊+专家），否决 MCP、向量库、pay_orders 列。

### 决策

只重构 `agent/` 编排层：deepagents（LLM 自由选动作）+ GuardMiddleware
（wrap_tool_call：args 校验、预算硬中断、trace/evidence 持久化兜底）+
RunOrchestrator（生命周期、后置 Reflection、repair 重入、终态化）。
确定性核心（guardrails/services/repositories/memory/evals）契约不变；
LLM 成为必需组件（不可用 → LLM_REQUIRED_UNAVAILABLE）。
完整设计见 docs/final-design/。

### 理由

LLM-first 更纯粹（彻底消除确定性主策略与 LLM 策略双轨）；deepagents middleware
可短路拒绝工具调用，零静默兜底语义可完整迁移；Multi-Agent 直接复用 subagent
机制。代价（eval 路径不确定性上升、图结构保证降级为 middleware 保证）已识别，
缓解为结果级判分 + 确定性预算 + Reflection 闸门。

### 被否决的方案

- 保持 LangGraph StateGraph（评审推荐项）：守卫最强，但与最终版 LLM-first
  目标不符，用户否决。
- 全盘 deepagents（含内置 filesystem 工具自由使用）：污染受控动作空间，禁用。

### 后续跟进

- P6 钉死版本：`deepagents==0.3.5`、`langchain==1.2.3`、
  `langchain-core==1.4.2`、`langchain-openai==1.2.2`、
  `langgraph==1.0.6`、`langgraph-checkpoint==3.0.1`、
  `langgraph-prebuilt==1.0.5`。Context7 官方 deepagents 文档核验了
  `create_deep_agent(model, tools, system_prompt, middleware, subagents,
  response_format, ...)` 与 `AgentMiddleware.wrap_tool_call(request, handler)`
  API；fix-002 已用 `uv pip install -e .` 在本地安装并校验这些 pins。
- P6 filesystem 工具治理已按本地安装的 pinned `deepagents==0.3.5` 源码解析：
  `create_deep_agent` 公开签名无 `permissions`/`builtin_tools` 参数，且会无条件组装
  `FilesystemMiddleware`，暴露 `ls/read_file/write_file/edit_file/glob/grep`
  （以及具备 sandbox backend 时的 `execute`）。MetricRCA 不调用该 helper；
  factory 改为复用 deepagents/LangChain 的核心 middleware 组合
  （`TodoListMiddleware`、summarization、prompt-caching、patch-tool-calls），明确省略
  `FilesystemMiddleware` 和 P9 前禁用的 subagent `task` 工具。生产构造后必须从真实
  compiled graph 的 ToolNode 读取工具集合并校验恰好为 MetricRCA 白名单 +
  `write_todos`；若无法内省或发现 filesystem 工具，typed fail-fast
  `DEEPAGENTS_FILESYSTEM_TOOLS_UNDISABLEABLE`。
- fix-002 收敛了 LLM 自由规划下的显式用户范围和 evidence id 语义：orchestrator
  从问题中抽取 `channel/category/device/product=value` 写入 GuardMiddleware；
  middleware 要求 `detect_anomaly` 与后续工具保持同一范围，并要求下游
  `evidence_ids` 使用当前 run 的完整 `{run_id}:E*` 前缀。证据槽重复调用只在同一
  run、同一 alias、`guard_status=passed` 且请求上下文匹配已持久化摘要时幂等返回；
  不匹配或真实写库失败仍 typed fail-fast。E_rank 不再允许用占位 SQL 补齐缺失
  E4 provenance；持久化 E4 缺 `sql_text` 时 typed fail-fast。compiled graph
  filesystem proof 将 deepagents 作为硬依赖，缺失安装不能 skip 后通过。
- v1 图设计在 docs/MetricRCA.md 中保留为附录（演变脉络）。

## ADL-0006: Final Report Is A Verified Artifact Projection Until P4 Persistence

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | p3b-reflection-memory |
| 影响范围 | generate_report, reflection, P4 API persistence |

### 背景与场景

P3B Reflection 已经能校验 current-run guard-passed Evidence、persisted
Evidence、repair path 和 memory pollution。P4 API/UI 将暴露 RCA run outputs，
因此 P3 的 final report 不能在 Reflection 之后新增未经验证的数字或因果结论。

### 决策

在 P3B 中，`generate_report` 只做已验证 artifact 的机械投影：

- 不暴露完整 `RootCauseCandidate` 数值字段。
- 只输出非数值候选身份字段：`root_cause_type`、`dimension`、`element`、`verdict`。
- 所有数值只允许出现在 `numeric_claims`。
- 每个 `numeric_claim` 必须绑定 persisted Evidence，当前为 E4。
- persisted E4 的 `result_summary.selected_candidate` 必须与 state top candidate 完全一致。
- failed 或 missing Reflection 不得生成 report。

### 理由

这能避免 P3 在 Reflection 之后生成未经验证的新数字，保护 P4 API/UI 和 P5
eval 的可观察边界。P4 仍需实现 report artifact persistence，不允许 GET route
返回内存态或硬编码 report。

### P4 前置要求

P4 必须选择并实现一种 report persistence 策略：

1. 在 `agent_run` 增加 `report_json` / `final_state_summary`；
2. 或新增 `report_artifact` 表；
3. 或从 persisted evidence/candidates/trace 做确定性重构。

无论选择哪种，API `GET /api/rca/runs/{run_id}` 都不能返回 route-level
hardcoded data，也不能依赖未持久化的 graph return state。

### P4 选定策略

P4 默认采用策略 3：从 persisted evidence/candidates/trace 做确定性重构，不新增表、不修改 P1 schema。

具体规则：

- API `GET /api/rca/runs/{run_id}` 读取 `agent_run`。
- succeeded run 读取 persisted `evidence`，尤其是 `{run_id}:E4`。
- E4 `result_summary.selected_candidate` 是 top candidate 的 persisted source of truth。
- report 只投影非数值 candidate identity fields 与 numeric_claims。
- numeric_claims 必须绑定 persisted Evidence。
- failed run 不返回 report。
- no_anomaly run 只允许 E1，不返回 candidate/task。
- 若 persisted E4 缺失或 malformed，返回 typed error，不伪造 report。

P4 可新增 `metric_rca/reporting/projector.py` 作为 graph report 与 API report
的共享投影层。该模块不得读取 fact tables，不得读取 anomaly_ground_truth，
不得调用 run_rca。

## ADL-0007: P5 Eval Scores Persisted Artifacts, Not Graph Return State

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | p5-eval-docs |
| 影响范围 | eval runner, scorer, API consistency, reporting projector |

### 背景与场景

P3B 和 P4 建立了 persisted artifact 边界：Evidence、TraceStep、SQL audit、
OperationTask、MemoryRecord 和 reconstructed report 是 RCA run 的可审计结果。
Eval 如果直接使用 `run_rca()` 的内存态返回进行评分，可能掩盖 artifact
persistence、report reconstruction、trace/evidence retrieval 的问题。

### 决策

P5 eval runner 可以调用 `run_rca()` 触发 RCA，但 scorer 必须从 persisted
artifacts 读取并评分：

- `agent_run`
- `evidence`
- `trace_step`
- `sql_audit`
- `operation_task`
- API/reporting projector reconstructed report

Eval scoring 不得依赖 graph invoke 的内存态返回作为最终判断来源。

新增 eval 指标：

- `report_traceable_ok`
- `memory_pollution_ok`
- `no_anomaly_task_ok`

### 理由

这保证 P5 测到的是系统对外可观察能力，而不是一次 Python 调用返回的临时对象。
P4 API/UI 也依赖同一 persisted artifact boundary，因此 eval 与 API 的判断口径必须一致。

### 被否决的方案

直接从 `run_rca()` 返回 state 中读取 `candidates/report/evidences` 打分被拒绝，
因为它绕过了持久化、API reconstruction 和 DB artifact consistency 的验证。
将 `dangerous_sql_blocked=True` 写成常量也被拒绝，必须来自真实 SQLGuard
negative behavior。

## ADL-0005: P3B Reflection Repair And Memory Stay Evidence-Bound

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | p3b-reflection-memory |
| 影响范围 | reflection verifier, graph repair routing, memory repository, report/task gates |

### 背景与场景

P3A established the real LangGraph/ReAct/Trace foundation, but Reflection still
needed full deterministic checks and Memory still needed a real repository over
`memory_record`. P3B also needed to prove that repair cannot pass by incrementing
`repair_count`, and that memory hits cannot become root-cause evidence or final
conclusions.

### 决策

Keep Reflection as a deterministic rule verifier. A repairable issue sets
`repair_pending=True`, increments `repair_count`, and provides a whitelisted
`AgentAction`; `react_step` consumes that action and `execute_tool` runs the
normal registry/tool/QuerySpec/Renderer/Guard/Repository path to create new
Evidence before Reflection can pass. Reflection must validate candidate evidence
against persisted `evidence` rows, including `query_spec` and `result_summary`
content consistency, not only state-held evidence objects. Add hard gates so
`generate_report` and `create_tasks` require passed Reflection except for
`no_anomaly`; final reports may expose only mechanically derived numeric claims
that are traceable to persisted evidence rows.

Implement `metric_rca.memory.memory_repo.MemoryRepository` as a real system-table
repository over `memory_record`, using exact `(layer, mem_key)` reads and
confidence, trusted source, TTL, and version filtering. Memory hits only
reorder drilldown priority through `memory_hits`; they are never accepted as
`evidence_id` values or direct conclusions. The `write_memory` node still runs
at graph termination, but `memory_record` persistence is intentionally limited
to reflection-verified successful candidate memory; failed, no-anomaly, and
candidate-free runs do not write memory records.

### 理由

The repair loop must remain auditable and reuse P3A's trace, action schema, SQL
guard, and evidence persistence boundaries. Letting Reflection execute tools
directly or marking repaired without new evidence would create a second,
untraced data path. Memory is useful as a planning prior, but accepting memory
as evidence would violate the core principle that facts come from current-run
queries and deterministic algorithms.

### 被否决的方案

Running repair queries inside `reflection_verify` was rejected because it would
bypass the ReAct/tool boundary. Treating optional memory failures as silent
no-ops was rejected; optional failures are trace warnings, while required
failures remain typed run failures. Using memory payload root-cause fields to
create candidates was rejected as a memory-derived conclusion shortcut.

### 后续跟进

P4/P5 API/UI/eval must surface Reflection issues, repair traces, and memory hits
from persisted graph outputs without changing the evidence boundary. Bounded SQL
execution retry remains a separate hardening task if the project decides to
implement it with a narrow retry policy.

## ADL-0004: P3A Requires Shared Trace, AgentRun Lifecycle, And Positive Proof Tests

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | p3a-preflight-prompt-hardening |
| 影响范围 | P3A prompt, graph nodes, trace, proof tests |

### 背景与场景

P2 review cycles found that absence-only tests were too weak: net_gmv initially
proved only that GMV-only decomposition was absent, while missing the required
gmv/refund split. Tool runtime fixes also showed that typed failures must cover
system table persistence, not only metric fact SQL execution. P3A will add
graph nodes, trace persistence, and agent_run lifecycle transitions, so those
boundaries must be specified before implementation starts.

### 决策

Harden the P3A iteration prompt before coding. Require a shared TraceWriter (or
equivalent) for trace_step seq, latency, and error_code persistence; require
agent_run lifecycle persistence for running/succeeded/no_anomaly/failed states;
require graph dependencies to be injectable in tests; require graph E2E parsing
through MetricService.parse_question and the live LLMIntentPlanner path; require
attribute_rank to use only current-run state evidence; and require no_anomaly to
produce exactly E1 with no downstream E2/E3/E4, candidates, tasks, or
attribute_rank trace.

### 理由

P3 nodes should orchestrate state, routing, trace, and typed failure propagation
without duplicating P2 tools or metadata services. A shared trace boundary avoids
per-node seq drift and inconsistent error mapping. AgentRun lifecycle tests make
fail-fast behavior observable to API/UI/eval layers later. Positive proof tests
prevent a shortcut from passing by merely not doing the wrong thing.

### 被否决的方案

Relying on graph-level generic exception handling was rejected because it would
hide typed error causes from trace_step and agent_run. Allowing MockIntentPlanner
in graph E2E tests was rejected because P2 intentionally made live LLM parsing
the production intent boundary. Leaving no_anomaly assertions at "no task" was
rejected because downstream evidence or candidate creation would still pollute
the run.

### 后续跟进

P3A implementation should start from the hardened prompt and add the named proof
tests before graph code. P3B should keep the same positive-proof standard for
reflection repair and memory pollution tests.

## ADL-0003: Tool Runtime Errors And Metadata Boundaries Stay Separate

| 字段 | 值 |
|------|------|
| 日期 | 2026-06-09 |
| 状态 | accepted |
| 关联迭代 | p2-pr-review-hotfix |
| 影响范围 | tool execution, metadata service, contribution evidence |

### 背景与场景

GPT Pro review found three P2 boundary leaks: tool modules let repository execution failures escape as raw exceptions, metadata-only methods were blocked by eager LLM planner construction, and `calculate_contribution` emitted GMV UV/PAY_CVR/AOV decomposition for non-GMV metrics.

### 决策

Add a shared `metric_rca.agent.tools.runtime` helper for run/evidence validation, guarded plan execution, evidence persistence, evidence row construction, query source summaries, and typed tool error mapping. Keep `MetricService` metadata methods independent from LLM provider availability by constructing `LLMIntentPlanner` lazily inside `parse_question()`. Restrict GMV factor decomposition to `metric_id="gmv"`; `net_gmv` receives its own guarded `gmv/refund/net_gmv` split; pay conversion and refund-rate contribution evidence reports the current metric's dimension delta summary instead of GMV-only factors.

### 理由

The future P3 graph needs tool failures as structured Observations so trace/error nodes can persist typed error codes. Metadata contracts in docs §13 are DB-backed and should be callable without OpenAI credentials. GMV decomposition is a metric-specific model; reusing it for pay conversion or refund rate creates misleading E4 evidence. Net GMV has a separate documented equation, `net_gmv = gmv - refund`, so the tool must emit that split at E4 rather than treating it as a generic non-factor metric.

### 被否决的方案

Wrapping tools in a graph-level exception catcher was rejected because P2 tools must already satisfy their typed contract. Creating a no-op or fake planner for metadata access was rejected as fallback-like behavior. Returning GMV factors for all metrics with a label change was rejected because the queried factors would still be unrelated to the requested metric. Leaving evidence persistence failures as raw repository exceptions was rejected because P2 tool outputs must be typed before P3 graph integration.

### 后续跟进

P3 should make `execute_tool` persist typed tool Observations into `trace_step` and `agent_run.error_code`. P5 should replace the current eval placeholder with a real runner and scorer over `anomaly_ground_truth`.

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
