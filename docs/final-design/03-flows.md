# 最终版关键流程（v2）

## 1. 标准 run（「昨天净 GMV 为什么下降？」，multi_agent=true）

| # | 执行者 | 动作 | 持久化 | 失败路径 |
|---|---|---|---|---|
| 1 | API | POST /api/rca/runs | agent_run(running) | 422 |
| 2 | Orchestrator | 构建 deep agent；将 user question + run context（target_date、metric_id 白名单、显式 dimension=value 过滤范围）发给 LLM；LLM 不可用 | — | LLM_REQUIRED_UNAVAILABLE → failed |
| 3 | 分诊 agent | parse intent → 路由 gmv_family expert | trace(triage_route) | PARSE_FAILED → failed |
| 4 | expert | detect_anomaly(net_gmv) | trace + E1 + audit | NO_ANOMALY → 流程 §2 |
| 5 | expert | calculate_contribution(net_gmv_chain) | trace + E2 | 工具 ok=False → LLM 换动作或结束 |
| 6 | expert | adtributor_attribute(gmv 侧, [channel,category,device]) | trace + E3 | ADTRIBUTOR_NOT_APPLICABLE → 单维下钻 |
| 7 | expert | fetch_related_signal(campaign) | trace + E4 | 重试 1 次 → 仍败 failed |
| 8 | expert | rank_root_causes | trace + E_rank | ATTRIBUTION_COVERAGE_LOW → 结构化证据不足 |
| 9 | expert | 结构化 RunOutcome 返回 | — | — |
| 10 | Orchestrator | Reflection 校验 persisted artifacts | — | issues → §3 repair |
| 11 | Orchestrator | report 投影 + create_tasks + write_memory(episodic) | report/task/memory | MEMORY_WRITE_FAILED → failed |
| 12 | API | 返回 succeeded；GET 全部从 persisted artifacts 重构 | agent_run(succeeded) | — |

每步 GuardMiddleware 先行：args 校验 → 预算计数 → 执行 → trace 落库（含 token_usage）。

## 2. no_anomaly 流程

detect_anomaly → is_anomaly=false → expert 按 prompt 直接结束 →
Orchestrator 校验「只有 E1、无下钻/rank trace、无 task」→ status=no_anomaly。
若 LLM 违约继续下钻：工具照常执行（middleware 不读业务语义），但终态化时
Orchestrator 判 `NO_ANOMALY_CONTRACT_VIOLATED` → failed。**宁可 fail 不可错报。**

## 3. Reflection repair 流程

1. 校验失败（如 unsupported_claim：candidate 缺信号证据），issue 带
   suggested_action（fetch_related_signal）。
2. repair_count(0) < max_repair(1) → orchestrator 向同一 thread 注入 repair
   消息（issue + 建议动作），agent 重入，新增动作仍经 middleware/预算。
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
- signal_type 与 metric/dimension 策略冲突：`fetch_related_signal` 返回
  recoverable `QUERY_SPEC_INVALID`，提示唯一合法 signal_type（例如
  `refund_rate + product -> refund_quality`），不执行查询。
- 预算耗尽：middleware 短路 BUDGET_EXCEEDED + 提示「只能 rank 或结束」；
  step 预算不拦截 rank_root_causes/write_todos，保证可收束；LLM 再越权调用
  data-fetching 工具 → failed。预算计数器在 run 上下文，multi-agent 模式下全局共享。

## 5. eval 流程（P5 管线沿用，扩到 20 case）

`make eval` → 逐 case 跑 run → **只读 persisted artifacts** 判分 →
eval_run/eval_case_result 落库 → JSON+Markdown 输出。新增逐 case 字段：
`adtributor_used`、`multi_agent_path`（路由 trace 摘要）；汇总新增
token/latency 统计。memory retrieval eval（P8）：同一 case 带/不带记忆命中
各跑一次，命中组正确率不得低于无命中组，且零污染断言。
