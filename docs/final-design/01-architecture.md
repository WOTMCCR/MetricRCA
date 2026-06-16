# 最终版架构（v2）— deepagents 编排 + 确定性核心

## 1. 分层总览

破坏性重构**只发生在 `metric_rca/agent/`**。其余层保留并增强：

```
FastAPI (api/，契约不变)
   │  POST /api/rca/runs  同步执行，返回前所有持久化副作用已落库
   ▼
RunOrchestrator (agent/runner.py，新)          ← 确定性，非 LLM
   ├── 1. 创建 agent_run 行（status=running）
   ├── 2. 构建 deep agent 并 invoke（LLM 必需）
   │      ├─ 分诊主 agent ──task──> expert subagent（MULTI_AGENT_ENABLED=true）
   │      │     experts: gmv_family / rate_family
   │      └─ 单 expert 直连（=false，默认；P6–P8 仅此模式）
   │      工具层（确定性，全部经 GuardMiddleware）：
   │        detect_anomaly / drilldown_dimension / fetch_related_signal /
   │        calculate_contribution / rank_root_causes（P7 内部调用 Adtributor）
   ├── 3. Reflection 校验（确定性，agent 循环之外）
   │      不过 → 一次 repair 重入 agent → 仍不过 → REFLECTION_REPAIR_FAILED
   ├── 4. report 投影（ADL-0006，persisted artifacts 重构，非 LLM 文本）
   ├── 5. create_tasks / write_memory
   └── 6. 终态化 agent_run（succeeded / no_anomaly / failed + error_code）

确定性核心（不变契约）：
  guardrails/  QuerySpec -> SQLRenderer -> SQLGuard（唯一取数路径）
  services/    metric_service(元数据唯一路径) anomaly attribution adtributor(新)
  repositories/  memory/  observability/  evals/  data/  domain/
```

## 2. agent/ 包 v2 结构

```
metric_rca/agent/
  runner.py          # RunOrchestrator：生命周期、终态化、repair 重入
  factory.py         # create_deep_agent 装配：model、tools、middleware、subagents
  middleware.py      # GuardMiddleware（wrap_tool_call）：见 §3
  tools/             # langchain @tool 包装确定性服务，每工具 Pydantic In/Out
  prompts.py         # 分诊/专家 system prompt（受控动作空间说明、禁止编造数值）
  reflection.py      # 确定性校验器（v1 逻辑保留，输入改为 persisted artifacts）
  subagents.py       # expert 子代理配置（P9；advisory RunOutcome）
```

Orchestrator 调用 agent 时会把 run context 附加到用户消息：当前
`target_date`、允许的 `metric_id` 白名单、“yesterday 解释为 target_date”
的业务日期规则，以及 `ParsedIntent` 中已经结构化解析出的显式过滤范围。
LLM 仍自由选择工具，但不得自行改写配置日期、发明 metric_id，或把用户指定的
过滤范围切换到其他维度/元素。

删除：`graph.py`、`state.py`、`react.py`、`nodes/`（及其测试，见 04 迁移清单）。

## 3. GuardMiddleware（守卫语义的唯一落点）

`@wrap_tool_call` 实现，职责按顺序：

1. **动作空间校验**：tool name ∉ 注册白名单 → 短路返回
   `ACTION_SCHEMA_INVALID`（理论上 deepagents 只暴露注册工具，此为纵深防御）。
2. **args schema 校验**：按工具的 Pydantic In 模型校验（extra="forbid"）；
   失败 → 短路 `ACTION_SCHEMA_INVALID`，记 error observation，不执行工具。
   单次非法调用可恢复，LLM 可用合法 args 重试；同名工具连续第 2 次非法
   才将 run 标记 failed。
   如果用户问题包含显式过滤条件（如 `category=electronics`），middleware
   会要求 `detect_anomaly` 先带同一过滤条件产生 E1；后续
   drill/fetch/calculate 必须保持同一维度和值；违反时返回 typed
   `ACTION_SCHEMA_INVALID`，不静默改写参数。显式范围错误属于可恢复 planning
   precondition，不参与 Pydantic schema 非法调用的连续失败计数。
   下游工具的 `evidence_ids` 必须精确使用当前 run 前缀（如
   `{run_id}:E1`），错误前缀/拼写返回 recoverable `EVIDENCE_MISSING`，不执行
   handler、不消耗预算。
   intent parsing 后的 run `target_date` 也是硬范围；任何后续工具传入不同
   `target_date` 时返回 recoverable `METRIC_SCOPE_VIOLATION`，同样不执行
   handler、不消耗预算。
3. **预算硬中断**（确定性计数器，存 run 级上下文对象，LLM 不可见不可改）：
   `step_count>=max_steps`、`query_count>=max_query`、
   `drilldown_depth>max_drilldown_depth` → 短路 `BUDGET_EXCEEDED`（新错误码）。
   step/query 耗尽向 LLM 返回「预算耗尽，必须调用 rank_root_causes 或结束」的
   typed 提示，并阻止后续非法 data tool；drilldown-depth 耗尽只禁止继续下钻，
   不阻止基于既有 E2 的 signal/contribution/rank。step 预算不拦截
   `rank_root_causes` 与 `write_todos`，确保 agent 可收束。
   已验证的幂等 `drilldown_dimension` 复用（已有 matching E2-family Evidence）
   不消耗 step/query/drilldown 预算；E2 复用以已存 metric/dimension/filters/E1
   上下文为准，LLM retry 中夹带的其它当前 run E2 id 不得导致重复查询或预算消耗。
   若预算耗尽后已有 E3 但尚无 E4，唯一
   允许的 data-tool 是匹配该 E3 链的 `calculate_contribution` E4 finalizer。
   query budget 耗尽仍拦截所有 data-tool；其它越权 data-tool → orchestrator
   终止 run（failed）。
4. **执行 + 持久化**：调用 handler；无论成败写 trace_step（含 latency_ms、
   token_usage 由模型回调补充）；取数类工具同时落 evidence + sql_audit
   （此逻辑在工具实现内部，middleware 负责兜底校验「取数工具必须产出
   evidence_id，否则视为工具实现缺陷 → typed error」）。
   对同一 run 的同一证据槽重复调用，工具只在已持久化 Evidence 为
   `guard_status=passed` 且请求上下文（E1: metric/filters；E2/E3/E4:
   metric/dimension/element/input evidence_ids）与已存摘要一致时幂等返回既有
   结果；不匹配或真实写库失败仍 fail-fast。已有 E1 但请求 scope 不同时返回
   recoverable `E1_ALREADY_EXISTS`，禁止落到主键冲突。
   `fetch_related_signal` 还会按 deterministic signal policy 校验
   metric/dimension → signal_type（如 `refund_rate` + `product` 必须为
   `refund_quality`）；错选返回 recoverable typed error，不执行查询。
   若 `fetch_related_signal` 携带 filters，则 filters 必须为空或精确等于当前
   `dimension=element`，否则视为 scoped action schema error，避免模型把已选
   元素和额外过滤条件混用。
   P7 发现型流程中，E2/E3 可使用 family alias（如 `E2_category`、
   `E3_ch_paid_ads`、`E3_cat_electronics`）以保留多维 evidence 且满足
   `evidence_id VARCHAR(64)`。在 E4 之前如果已有 E3-family evidence，
   middleware 以 recoverable `E3_ALREADY_EXISTS` 拒绝额外 signal fetch，
   提示调用 `calculate_contribution`；多元素/跨维排序由 `rank_root_causes`
   读取 E2 drilldown Evidence 完成。`E3_ALREADY_EXISTS` 优先于
   `DiscoveryPolicy` 的 first-signal retry 提示，避免已有 E3 后继续引导 fetch。
   `calculate_contribution` 必须与传入的 E3-family Evidence 使用同一
   dimension/element，并且 evidence_ids 必须包含匹配的 E2-family alias
   （如 `E3_prod_2` 必须配 `E2_product`），不能把 product signal 和 category
   contribution 目标混算。E4 selected candidate 必须从匹配 E3 的
   `signal_type`/`signal_metric_id` 继承 root_cause_type；例如
   `refund_quality`/`complaint_rate` 证据应得到 `complaint_or_quality_issue`，
   不能继续沿用 E2 product delta 的默认 `stockout`。`fetch_related_signal` 生成 E3 前也必须要求匹配的
   E2-family Evidence；不能用 `E2_category` 生成 `E3_prod_*`。若
   `DiscoveryPolicy` 要求 top candidate，但对应 E2 result 缺少结构化 candidates，
   middleware 必须 fail closed，而不是放任任意 element 通过。
   LLM intent planner 将自然语言发现语义解析成
   `ParsedIntent.analysis_strategy`，orchestrator 再生成结构化
   `DiscoveryPolicy` 注入 middleware；middleware 不解析原始 question 文本。
   对无显式过滤的 GMV discovery，policy 要求先有 guard-passed
   `E2_channel`、`E2_category`、`E2_product`，再允许 `fetch_related_signal`
   或 `rank_root_causes`，防止模型只看第一个显著 channel 后过早收束。
   `analysis_strategy=channel_first` 时，首个 E3/E4 必须来自
   `dimension=channel`、`signal_type=campaign`，但不强制使用 `E2_channel`
   top candidate element；若非 top channel 的 related signal evidence 更强，
   E4 可以验证该 selected element。`analysis_strategy=organic_first` 进一步通过
   `DiscoveryPolicy.first_signal_element=organic` 指定首个 channel/campaign signal 的
   element，middleware 只执行该结构化字段，不解析原始 question 文本。
   `analysis_strategy=product_first` 时，首个 E3/E4 必须来自
   `dimension=product`、`signal_type=inventory`，并使用 `E2_product` 的 top
   candidate element，让 E4 decomposition 验证 `aov_drop`。
   `rank_root_causes` 只能从持久化 E4 派生 E_rank；E4 缺失 candidates 或
   `sql_text` 时返回 typed error，不合成占位 SQL。
5. **失败语义**：工具返回 ok=False observation；可由 LLM 修正的 typed
   precondition/planning 错误（如 EVIDENCE_MISSING、DIMENSION_NOT_ALLOWED、
   QUERY_SPEC_INVALID）原样透传且不立刻终止，也不消耗 run 预算；retryable（仅
   SQL_EXECUTION_FAILED）由工具内部重试 1 次；不可恢复错误（如
   INSUFFICIENT_BASELINE_DATA）原样透传给 LLM，LLM 只能换合法动作或结束，
   **不存在任何静默兜底路径**。agent loop 结束后仍未绑定到 tool trace 的 pending
   token usage 必须写入 `llm_call` trace_step；该 final token trace 对
   `SYSTEM_TABLE_WRITE_FAILED` 只允许 bounded typed retry，重试耗尽仍失败，不丢弃
   observability 数据伪装成功。

### 3.1 P7 守卫增量（ADL-0009）

- **run 级 target-metric 不变量**：run 的 metric 锚定到 **parsed intent**
  （`MetricService.parse_question` 的 LLM 解析结果，经白名单校验），而非「首个工具
  调用里的 metric」。此后任何工具若传入不同 metric_id → recoverable typed error
  （`METRIC_SCOPE_VIOLATION`），不执行、不消耗预算，防止跨指标 evidence 污染
  （E1/E2 是 uv 而 E4 是 gmv 这类）。锚到 intent 而非首调用，避免一次漂移把整个
  run 锁死在错误指标。
- **工具↔schema 单一真相源**：GuardMiddleware 的 `tool_arg_schemas` 必须从工具注册
  表派生（注册即带 In-schema），不得维护第二份手写清单。加测试断言「每个白名单
  data 工具都有已注册 In-schema」，杜绝「工具暴露但 schema 漏注册 →
  ACTION_SCHEMA_INVALID」这类接线缺陷。

## 4. 内置工具治理

- **禁用 deepagents 内置 filesystem 工具集**
  （ls/read_file/write_file/edit_file/glob/grep/execute）：非审计副作用，
  污染动作空间。P6 pinned `deepagents==0.3.5` 的 `create_deep_agent`
  无条件组装 `FilesystemMiddleware` 且没有 `permissions`/`builtin_tools`
  移除开关；MetricRCA 因此按该版本源码组合 deepagents 核心 middleware，
  明确省略 `FilesystemMiddleware` 与 subagent `task` 工具（P9 前禁用），并用
  真实 compiled graph 的 ToolNode 工具集合测试证明暴露工具恰好等于
  MetricRCA 白名单 + planning todo 工具。若 pinned API 形态变化导致无法证明
  文件系统工具未暴露，factory typed fail-fast。该 proof test 将 deepagents 作为
  硬依赖，缺失安装不能 skip 后通过。
- **保留 planning（write_todos）工具**：仅记录规划，不产生事实、不访问 DB；
  其调用同样落 trace_step。

## 5. Reflection 与 repair（移出循环，语义不变）

- 校验器输入从图内存态改为 **persisted artifacts**（evidence / trace_step /
  rank 结果），检查清单与 v1 相同（evidence_coverage、metric_consistency、
  time_range_consistency、sql_guard_status、attribution_coverage、
  unsupported_claim、insufficient_data、correlation_vs_causation）。
- error 级 issue 且 repair_count < max_repair：orchestrator 以
  「ReflectionIssue + suggested_action」构造 repair 消息重入同一 agent thread
  （checkpointer 续上下文），repair prompt 写入 exact suggested JSON args 并禁止
  text-only response，同时将 suggested_action.action 注入
  `RunGuardContext.required_repair_action`。repair 动作同样经 GuardMiddleware；
  repair turn 中非该 suggested tool 的调用返回 recoverable `ACTION_SCHEMA_INVALID`
  且不消耗预算，避免模型重跑 detect/drilldown 而错过真正缺口。
- 若首次 agent pass 过早停止且没有候选，但 persisted E2 drilldown Evidence
  已存在，Reflection 必须基于 E2 top candidate 生成 `fetch_related_signal`
  suggested_action；若已有 E3 但缺 E4，则生成匹配 E3 链的
  `calculate_contribution` suggested_action（filters 从 parsed scope 或 persisted E1
  summary 继承）；coverage 不足且已有 E4 时生成
  `rank_root_causes` suggested_action。不得让 repair 轮靠模型自由猜缺口。
- 仍不过 → `REFLECTION_REPAIR_FAILED`，run failed，绝不编造主因。
- 如果 deepagents 在 terminal artifacts 已持久化后（no_anomaly E1，或完整
  E4+E_rank 证据链）遇到 transient provider error（rate limit/timeout），
  orchestrator 可继续 deterministic Reflection/report projection；同样错误在
  证据链不完整时仍 fail-fast。

## 6. no_anomaly 分支

detect_anomaly 返回 is_anomaly=false 时，expert prompt 约定必须直接结束
（不得下钻、不得调用 rank_root_causes）。**强制保证在 orchestrator**：
终态化时若 anomaly evidence 判定无异常但存在 drilldown/rank trace 或
operation_task → run failed（`NO_ANOMALY_CONTRACT_VIOLATED`，新错误码），
eval 的 `no_anomaly_correct` 继续按 v1 标准判分。

## 7. Multi-Agent（P9，开关式）

- `Settings.multi_agent_enabled: bool = False`。
- true：RunOrchestrator 用纯 Python triage 按 `ParsedIntent.metric_id` 做路由
  （无 LLM 调用、无取数工具），调用对应 expert；experts 共享同一工具集与 GuardMiddleware，预算计数器
  **run 级共享**（防止 subagent 重置预算）。
- expert prompt asks for advisory `RunOutcome` tracing/diagnostic output；
  malformed/mismatched output 只记录 warning，真值永远来自 persisted artifacts。
- 路由决策写 trace_step（node=`triage`, action=`route_{family}`）。
- false：跳过分诊，单 expert 直连。差分测试：同一 case 两种模式下
  score 字段结构一致、trace 含 triage step、预算共享/no-anomaly/repair 合约保留；
  不要求 LLM top1 结果逐字一致。

## 8. 可观测性增强

- trace_step 新增 `token_usage` JSON 列（prompt/completion/total per LLM call，
  经模型 usage 回调收集；唯一 DDL 变更）。
- run 总 token / 总时延汇总进 agent_run 投影（API 不破坏现有 schema，
  新增可选字段）。
- UI 新面板：Adtributor 候选（EP/surprise 排序）、记忆分层视图、token/latency
  看板。UI 安全要求沿用设计文档 §19（只读 persisted projection）。
