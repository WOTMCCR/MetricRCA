# MetricRCA 1 个月最终版（v2）— 总览与选型决策

> 状态：accepted（2026-06-12 用户批准）。本目录是 1 个月最终版的设计源（design source of truth），
> 与 `docs/MetricRCA.md` 冲突时以本目录为准；P6 阶段必须把冲突部分反向同步到
> `docs/MetricRCA.md` 与 `docs/COMPLIANCE_MATRIX.md`（先改文档与矩阵，再改代码，
> 见 AGENTS.md 合约）。

## 1. 目标

在已完成的 MVP（P1–P5，5 case 全绿，API/UI/Eval 已收口）基础上，交付设计文档
§22 定义的 1 个月最终版，并执行一次经用户批准的破坏性重构：

1. **编排层迁移到 deepagents**：`metric_rca/agent/` 由手写 LangGraph StateGraph
   迁移为 deepagents（LLM 自由选动作 + middleware 守卫），LLM 成为必需组件。
2. **Adtributor 多维归因**（解释力 EP + JS 散度 Surprise）+ 净 GMV 链路分解。
3. **20 类异常 case 库** 与对应 eval 扩展。
4. **四层记忆**（semantic / episodic / reflection + 冻结的 legacy case）+ 记忆检索 eval。
5. **可观测性增强**：token usage、UI 新面板。
6. **Multi-Agent（分诊 + 专家）**：开关式（`MULTI_AGENT_ENABLED=false` 时单 expert 行为不变）。

## 2. 范围裁剪（用户决策，2026-06-12）

| 可选项 | 决策 | 理由 |
|---|---|---|
| deepagents 迁移 | **做（破坏性重构）** | 用户明确选择；LLM-first 方向更纯粹 |
| Multi-Agent 分诊+专家 | **做（开关式）** | 与 deepagents subagent 机制天然契合 |
| MCP server 暴露工具 | 不做 | 「不为堆概念牺牲稳定性」 |
| 向量检索记忆 | 不做 | 20 case 规模下 key 精确匹配已够 |
| `fact_traffic.pay_orders` 列 | 不做 | 保留近似分解口径，避免 DDL/Renderer/算法/测试五处联动 |

## 3. 核心选型论证：deepagents 替代手写 LangGraph StateGraph

### 3.1 事实核验（2026-06-12，context7 / docs.langchain.com）

- deepagents = LangChain 官方的「规划 + 文件系统 + 子代理 + 自由 tool-calling」
  agent harness，构建于 LangGraph 之上。
- `wrap_tool_call` middleware 可拦截每次工具调用：校验、改写、**短路拒绝**
  （不调用 handler 直接返回错误 ToolMessage）。这是守卫与预算的落点。
- subagent 可提示输出 advisory `RunOutcome`，但 persisted artifacts 仍是事实源；
  `CompiledSubAgent` 可挂任意自定义 LangGraph 图。
- 官方明确：「需要自定义工作流控制时用 LangGraph」。即本迁移是**有意识地用
  prompt + middleware 替代图结构保证**，代价与缓解见 §5。

### 3.2 守卫语义的迁移映射

| v1 保证机制（图结构） | v2 保证机制（deepagents） |
|---|---|
| 条件边路由非法动作 → error_return | GuardMiddleware 校验 args schema → 短路返回 typed error ToolMessage |
| 节点写 trace_step | GuardMiddleware 在每次工具调用前后持久化 trace/evidence/sql_audit |
| `max_steps/max_query` 在 router 判定 | GuardMiddleware 确定性计数器硬中断（LLM 不可绕过） |
| 确定性主策略选动作 | LLM 选动作（temperature=0），动作空间 = 白名单工具集 |
| LLM 可选 | **LLM 必需**：不可用 → `LLM_REQUIRED_UNAVAILABLE`，无任何回退 |
| reflection_verify 节点 + 修复边 | Reflection 校验器移出 agent 循环，由 RunOrchestrator 后置执行；一次 repair 重入 |

不变量（v1→v2 原样保留）：

- `QuerySpec -> SQLRenderer -> SQLGuard -> Repository` 是唯一取数路径；
  LLM 永远不写 SQL。
- `MetadataRepository -> MetricService` 是唯一元数据路径；不得硬编码指标/维度/family。
- ADL-0006：final report 是 persisted artifact 的机械投影，数值只出现在
  `numeric_claims` 且必须绑定 persisted Evidence。LLM 自由文本永远不是数值来源。
- 零静默兜底：所有错误码表（设计文档 §18）继续成立，仅执行点位变化。

### 3.3 被否决的方案

- **保持 LangGraph StateGraph**（本次评审推荐项，被用户否决）：守卫更强但与
  「LLM-first、最终版要验证 LLM 规划能力」的目标不符。
- **全盘 deepagents（含内置 filesystem/planning 工具自由使用）**：内置
  filesystem 工具污染动作空间、引入非审计副作用，必须禁用（见 01 架构文档）。

## 4. 验收门槛（最终版 Definition of Done）

`make up && make seed && make api && make ui && make eval && make test` 全部可用，且：

| 指标 | 门槛 |
|---|---|
| intent-parse accuracy | 20/20 |
| anomaly-detection accuracy | 20/20（含 no-anomaly 类 case 零误报） |
| root-cause top-1 | ≥ 80% |
| root-cause top-3 | ≥ 90% |
| SQL safety（guard passed 比例） | 100% |
| report_traceable_ok / memory_pollution_ok / no_anomaly_correct | 100% |
| Multi-Agent 开关关闭 | 行为与单 expert 完全一致（差分测试证明） |

eval 不达标时**只允许修系统，不允许降门槛**；LLM/基础设施抖动导致的 flaky case 必须
归因（trace 复盘）。允许 eval runner 对明确 typed transient 错误做有界自动 retry
并记录 `eval_attempts`，但不得手工重跑刷绿、不得改题面/真值/阈值掩盖失败。最终验收要求连续
2 次 `make eval` 全绿。

## 5. 已知风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM 选动作漏查关键信号 | Reflection evidence_coverage 闸门：不过 → 一次 repair → 仍不过 typed error，绝不编造 |
| eval 路径不确定性上升 | eval 本就是结果级判分；预算/守卫是确定性硬约束；temperature=0 |
| deepagents/langchain API 演进快 | P6 落地时在 pyproject 钉死精确版本并记入 ADL；prompt 要求 Codex 对照钉死版本的官方文档核验 API |
| 测试作废成本 | agent 图相关测试（约 1/3）按 04-phase-plan 的迁移清单重写，证明测试先行 |
| 旧 `case` 记忆层与新分层冲突 | case 层冻结只读，新写入走 episodic（见 02 接口文档 §5） |
