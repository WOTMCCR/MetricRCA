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
