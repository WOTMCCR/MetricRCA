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
   │        calculate_contribution / adtributor_attribute(P7) / rank_root_causes
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
  subagents.py       # expert 子代理配置（P9；response_format=RunOutcome）
```

删除：`graph.py`、`state.py`、`react.py`、`nodes/`（及其测试，见 04 迁移清单）。

## 3. GuardMiddleware（守卫语义的唯一落点）

`@wrap_tool_call` 实现，职责按顺序：

1. **动作空间校验**：tool name ∉ 注册白名单 → 短路返回
   `ACTION_SCHEMA_INVALID`（理论上 deepagents 只暴露注册工具，此为纵深防御）。
2. **args schema 校验**：按工具的 Pydantic In 模型校验（extra="forbid"）；
   失败 → 短路 `ACTION_SCHEMA_INVALID`，记 error observation，不执行工具。
3. **预算硬中断**（确定性计数器，存 run 级上下文对象，LLM 不可见不可改）：
   `step_count>=max_steps`、`query_count>=max_query`、
   `drilldown_depth>max_drilldown_depth` → 短路 `BUDGET_EXCEEDED`（新错误码），
   并向 LLM 返回「预算耗尽，必须调用 rank_root_causes 或结束」的 typed 提示；
   若 LLM 再次尝试越权工具 → orchestrator 终止 run（failed）。
4. **执行 + 持久化**：调用 handler；无论成败写 trace_step（含 latency_ms、
   token_usage 由模型回调补充）；取数类工具同时落 evidence + sql_audit
   （此逻辑在工具实现内部，middleware 负责兜底校验「取数工具必须产出
   evidence_id，否则视为工具实现缺陷 → typed error」）。
5. **失败语义**：工具返回 ok=False observation；retryable（仅
   SQL_EXECUTION_FAILED）由工具内部重试 1 次；不可恢复错误（如
   INSUFFICIENT_BASELINE_DATA）原样透传给 LLM，LLM 只能换合法动作或结束，
   **不存在任何静默兜底路径**。

## 4. 内置工具治理

- **禁用 deepagents 内置 filesystem 工具集**（ls/read_file/write_file/edit_file）：
  非审计副作用，污染动作空间。P6 落地时按钉死版本的官方 API 确认禁用参数
  （`builtin_tools=[]` 或等价配置），并写差分测试证明 agent 暴露的工具集合
  恰好等于我们注册的白名单 + planning todo 工具。
- **保留 planning（write_todos）工具**：仅记录规划，不产生事实、不访问 DB；
  其调用同样落 trace_step。

## 5. Reflection 与 repair（移出循环，语义不变）

- 校验器输入从图内存态改为 **persisted artifacts**（evidence / trace_step /
  rank 结果），检查清单与 v1 相同（evidence_coverage、metric_consistency、
  time_range_consistency、sql_guard_status、attribution_coverage、
  unsupported_claim、insufficient_data、correlation_vs_causation）。
- error 级 issue 且 repair_count < max_repair：orchestrator 以
  「ReflectionIssue + suggested_action」构造 repair 消息重入同一 agent thread
  （checkpointer 续上下文），repair 动作同样经 GuardMiddleware。
- 仍不过 → `REFLECTION_REPAIR_FAILED`，run failed，绝不编造主因。

## 6. no_anomaly 分支

detect_anomaly 返回 is_anomaly=false 时，expert prompt 约定必须直接结束
（不得下钻、不得调用 rank_root_causes）。**强制保证在 orchestrator**：
终态化时若 anomaly evidence 判定无异常但存在 drilldown/rank trace 或
operation_task → run failed（`NO_ANOMALY_CONTRACT_VIOLATED`，新错误码），
eval 的 `no_anomaly_correct` 继续按 v1 标准判分。

## 7. Multi-Agent（P9，开关式）

- `Settings.multi_agent_enabled: bool = False`。
- true：分诊主 agent 仅做路由（无取数工具），按 ParsedIntent.family 调用
  expert subagent；experts 共享同一工具集与 GuardMiddleware，预算计数器
  **run 级共享**（防止 subagent 重置预算）。
- expert `response_format=RunOutcome`（仅 status_hint 与最终 rank evidence_id
  引用，不含自由数值——真值永远来自 persisted artifacts）。
- 路由决策写 trace_step（node=triage_route）。
- false：跳过分诊，单 expert 直连。差分测试：同一 case 两种模式下
  persisted artifacts 投影一致（evidence 序列允许不同，结论与判分字段一致）。

## 8. 可观测性增强

- trace_step 新增 `token_usage` JSON 列（prompt/completion/total per LLM call，
  经模型 usage 回调收集；唯一 DDL 变更）。
- run 总 token / 总时延汇总进 agent_run 投影（API 不破坏现有 schema，
  新增可选字段）。
- UI 新面板：Adtributor 候选（EP/surprise 排序）、记忆分层视图、token/latency
  看板。UI 安全要求沿用设计文档 §19（只读 persisted projection）。
