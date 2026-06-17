# 最终版关键流程（v2）

## 1. 标准 run（「昨天净 GMV 为什么下降？」，multi_agent=true）

| # | 执行者 | 动作 | 持久化 | 失败路径 |
|---|---|---|---|---|
| 1 | API | POST /api/rca/runs | agent_run(running) | 422 |
| 2 | Orchestrator | 构建 deep agent；将 user question + run context（target_date、metric_id 白名单、ParsedIntent 结构化过滤范围、parsed analysis_strategy、DiscoveryPolicy 摘要）发给 LLM；LLM 不可用 | — | LLM_REQUIRED_UNAVAILABLE → failed |
| 3 | Orchestrator triage | ParsedIntent.metric_id → route_gmv_family / route_rate_family | trace(node=triage, action=route_{family}) | METRIC_NOT_FOUND → failed |
| 4 | expert | detect_anomaly(net_gmv) | trace + E1 + audit | NO_ANOMALY → 流程 §2 |
| 5 | expert | drilldown_dimension（发现型可在预算内做多个 E2-family 下钻；无显式过滤 GMV 必须覆盖 channel/category/product） | trace + E2 / E2_* | 工具 ok=False → LLM 换动作或结束 |
| 6 | expert | fetch_related_signal(campaign) | trace + E3 / E3_*（如 `E3_ch_paid_ads`） | 重试 1 次 → 仍败 failed |
| 7 | expert | calculate_contribution(net_gmv_chain 或 selected slice) | trace + E4 | 工具 ok=False → LLM 换动作或结束 |
| 8 | expert | rank_root_causes（内部按需调用 Adtributor） | trace + E_rank；更新 E4 candidates EP/surprise | ATTRIBUTION_COVERAGE_LOW → 结构化证据不足 |
| 9 | expert | advisory RunOutcome 返回（malformed 只 warning） | — | — |
| 10 | Orchestrator | Reflection 校验 persisted artifacts | — | issues → §3 repair |
| 11 | Orchestrator | report 投影 + create_tasks + write_memory(episodic) | report/task/memory | MEMORY_WRITE_FAILED → failed |
| 12 | API | 返回 succeeded；GET 全部从 persisted artifacts 重构 | agent_run(succeeded) | — |

每步 GuardMiddleware 先行：args 校验 → evidence flow 校验 → 结构化
DiscoveryPolicy 校验 → 预算计数 → 执行 → trace 落库（含 token_usage）。
发现型问题可在 E4 前做多个 E2-family 下钻；无显式过滤的 GMV discovery 在
`fetch_related_signal`/`rank_root_causes` 前必须已有 `E2_channel`、
`E2_category`、`E2_product`。自然语言 discovery 语义只由 LLM intent planner
写入 `ParsedIntent.analysis_strategy`，再由 orchestrator 转成 `DiscoveryPolicy`；
middleware 不重新解析 question 文本。标准无显式过滤 GMV discovery 与
`channel_first` policy 要求首个 E3/E4 chain 使用 `dimension=channel`、
`signal_type=campaign`，并绑定 `E2_channel` top candidate；`signal_first`
policy 使用相同的 channel/campaign first-signal 约束，并额外要求
`element_selection=signal_anomaly`，该 element 来自 current-run signal anomaly evidence
而不是 middleware keyword parser；`product_first` policy
要求首个 E3/E4 chain 选 `E2_product` top candidate 并使用
`signal_type=inventory`，由 E4 decomposition 验证 `aov_drop`。首个 E3-family signal 成功后必须进入
calculate_contribution；额外 `fetch_related_signal` 被 `E3_ALREADY_EXISTS`
recoverable 拒绝，不消耗预算。

## 2. no_anomaly 流程

detect_anomaly → is_anomaly=false → expert 按 prompt 直接结束 →
Orchestrator 校验「只有 E1、无下钻/rank trace、无 task」→ status=no_anomaly。
若 LLM 违约继续下钻：工具照常执行（middleware 不读业务语义），但终态化时
Orchestrator 判 `NO_ANOMALY_CONTRACT_VIOLATED` → failed。**宁可 fail 不可错报。**

## 3. Reflection repair 流程

1. 校验失败（如 unsupported_claim：candidate 缺信号证据），issue 带
   suggested_action（fetch_related_signal）。
   no-candidate 但已有 E2 drilldown 时，suggested_action 从 persisted E2
   top candidate 生成 `fetch_related_signal`；已有 E3 但缺 E4 时生成匹配
   `calculate_contribution`；coverage 不足且已有 E4 时生成 `rank_root_causes`，
   不允许 repair 轮自由猜缺口。
2. repair_count(0) < max_repair(1) → orchestrator 向同一 thread 注入 repair
   消息（issue + 建议动作 + exact suggested JSON args，并禁止文本回答），并把
   suggested_action.action 注入 `RunGuardContext.required_repair_action`。agent 重入后，
   第一 repair tool 必须是 suggested tool；后续只允许当前 run persisted Evidence 证明的
   E3→E4→E_rank 续步（如补 E3 后调用 `calculate_contribution`，E4 后调用
   `rank_root_causes`）。其它非 suggested/非证据续步的调用被 recoverable
   `ACTION_SCHEMA_INVALID` 拒绝且不消耗预算。
3. 重新校验：passed → 继续投影；仍 error → REFLECTION_REPAIR_FAILED，failed。

## 4. 守卫拒绝 / 预算耗尽流程

- args 非法：middleware 短路 ACTION_SCHEMA_INVALID → LLM 收到 typed error，
  只能改用合法 args；连续 2 次同名工具非法 → orchestrator 终止 run（failed）。
- 显式问题范围非法：如果问题中包含 `channel/category/device/product=value`，
  `detect_anomaly` 和后续 drill/fetch/calculate 必须保持同一过滤范围；缺失或
  切换维度/元素时短路可恢复 `ACTION_SCHEMA_INVALID`，提示 exact retry
  方向；该 precondition 错误不参与 schema 非法调用的连续失败计数。
- evidence id 非当前 run 前缀：middleware 短路 recoverable
  `EVIDENCE_MISSING`，提示复制此前工具返回的完整 `{run_id}:E*`，不执行工具、
  不消耗预算。
- 工具层发现缺失 E-family 证据时，也必须在 `EVIDENCE_MISSING` 消息中列出
  当前 run 已持久化的 exact `E1`/`E2_*`/`E3_*` ids，不能提示模型改用不存在的
  裸别名。
- target_date 非当前 run 日期：middleware 短路 recoverable
  `METRIC_SCOPE_VIOLATION`，提示使用 run target_date，不执行工具、不消耗预算。
- 无显式过滤 GMV discovery 过早 fetch/rank：如果缺少 `E2_channel`、
  `E2_category` 或 `E2_product`，middleware 短路 recoverable
  `EVIDENCE_MISSING`，提示先补缺失 drilldown。
- signal_type 与 metric/dimension 策略冲突：`fetch_related_signal` 返回
  recoverable `QUERY_SPEC_INVALID`，提示唯一合法 signal_type（例如
  `refund_rate + product -> refund_quality`），不执行查询。
- `fetch_related_signal.filters` 与所选 dimension/element 不一致：middleware
  短路 recoverable `ACTION_SCHEMA_INVALID`；deterministic tool 层也以
  `QUERY_SPEC_INVALID` 拒绝该组合。
- E4 前重复拉取 signal：若 run 内已有 guard-passed E3-family evidence，
  middleware 返回 recoverable `E3_ALREADY_EXISTS`，提示用既有 E3 调
  `calculate_contribution`；这是 P7 Adtributor 路径的硬约束，避免逐元素 E3
  fetch 耗尽预算。该提示必须把已有 E3 与其匹配的 E2-family id 配对，例如
  `E3_ch_*` 配 `E2_channel`，不得混用本次失败请求里的其它 E2-family id；
  该规则优先于 channel-first/product-first retry 提示。
- 预算耗尽：middleware 短路 BUDGET_EXCEEDED + 提示「只能 rank 或结束」；
  step 预算不拦截 rank_root_causes/write_todos，保证可收束；已验证的
  matching E2-family drilldown 复用不消耗预算，即使 retry 中夹带其它当前 run
  E2 id 也直接返回已存 E2；已有 E3 且尚无 E4 时，匹配该 E3 链的
  calculate_contribution 可作为 E4 finalizer 完成证据链。除此之外，LLM 再
  越权调用 data-tool 使 run failed。
- merchandise/price/AOV GMV discovery 的 product-first signal 必须使用
  `E2_product` top candidate 的 element；middleware 对不匹配 element 返回
  recoverable `ACTION_SCHEMA_INVALID`，避免模型把 category/channel element
  当成 product element。
  data-fetching 工具 → failed。预算计数器在 run 上下文，multi-agent 模式下全局共享。

## 4.A 终态证据后的 transient LLM 错误

deepagents 的最后一次自然语言收束不是事实来源；事实来源是 persisted Evidence +
Reflection + deterministic projector。若模型在 no_anomaly E1 或完整 E4+E_rank 已
持久化之后返回 `rate_limit_exceeded` / timeout 等 transient provider error，
RunOrchestrator 可继续 deterministic Reflection/report。若 terminal artifacts 不完整，
相同错误仍 fail-fast。

## 5. eval 流程（P5 管线沿用，扩到 20 case）

`make eval` → 逐 case 跑 run（case 间可用 `METRIC_RCA_EVAL_CONCURRENCY`
并发，默认 1；单个 RCA run 内仍顺序执行 evidence loop）→ **只读 persisted
artifacts** 判分 → eval_run/eval_case_result 落库 → JSON+Markdown 输出。新增逐 case 字段：
`adtributor_used`、`multi_agent_path`（路由 trace 摘要）；汇总新增
token/latency 统计。并发 worker 使用独立 repository/orchestrator/trace writer；
主线程按 future 完成顺序收集结果、按 case 输入 index 复原最终输出顺序，并最后写
`eval_run.summary`；baseline eval leg 禁用 memory，P8 memory retrieval leg 成对运行
memory enabled/disabled。eval runner 只对明确 typed transient
LLM 错误（rate/timeout/unavailable）做有界同 case retry（`eval_llm_max_attempts`
默认 3 且必须 ≥1），`eval_attempts` 入 detail。`SYSTEM_TABLE_WRITE_FAILED` 只在
repository 写入边界做有界 retry；INSERT retry 必须有稳定幂等键并在 duplicate 后确认已提交
payload，耗尽后在 eval case 层 fail-fast，避免整案重跑掩盖 schema/payload 错误。任何 typed
failed/missing/unknown-status run 不进入 scorer，避免系统错误或非终态 artifact 被降级为阈值失败；
最终仍按成功 attempt 的 persisted artifacts 判分。memory retrieval eval
（P8）：同一 case 带/不带记忆命中各跑一次，命中组正确率不得低于无命中组，且零污染断言。
HTTP eval 复用相同 typed LLM transient retry 口径，且不对系统表写失败做整案重跑。
