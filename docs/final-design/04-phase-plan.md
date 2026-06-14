# 最终版阶段计划（P6–P9）与 Codex 分发模式

## 0. 分发与监督模式（沿用现有）

- 每阶段一个 prompt（`docs/iteration-prompts/07~10`），前缀引用
  `00-global-iteration-rules.md` 与 `06-review-checklist.md`。
- 每阶段独立分支 `codex/p6-deepagents-core` … `codex/p9-multiagent-final`。
- Codex（gpt-5.5-high-fast）执行；Claude 按 06-review-checklist 对抗审查，
  重点：硬编码与数据源（§13「访问 DB」列逐行核对）、零兜底证明测试是否真能
  拦截 shortcut、文档/矩阵是否先于代码更新。
- 审查不过 → 修复 prompt（fix-00N）→ 复审 → 合并 main。

## P6（07）deepagents 核心迁移 — codex/p6-deepagents-core

1. **文档先行**：按 final-design 更新 `docs/MetricRCA.md` §5/§6（标注 v2 取代
   v1 图设计，保留 v1 章节为附录）、`docs/COMPLIANCE_MATRIX.md`（图结构相关行
   改写为 middleware/orchestrator 行，逐行给出新证明测试）。
2. pyproject 钉死 deepagents/langchain 精确版本，记 ADL。
3. `agent/` 重写（runner/factory/middleware/tools/prompts/reflection），删除
   graph/state/react/nodes。
4. 内置 filesystem 工具禁用 + 工具集合差分测试。
5. 零兜底负向测试 v2 全量重写（LLM 不可用、非法 args、预算越权、guard 拒绝
   不可绕过、no_anomaly 违约、repair 耗尽、memory 失败、空结果不归因）。
6. trace_step.token_usage 列 + 采集。

验收：原 5 case 全绿（结果级）、SQL safety=100%、`make test` 全绿、
COMPLIANCE_MATRIX 无红行。

## P7（08）Adtributor + 净 GMV + 20 case — codex/p7-adtributor-20cases

1. adtributor_service（EP/JS/贪心选择 + 阈值 Settings）+ 单测（论文数值例）。
2. rank_root_causes 内部确定性调用 Adtributor + RootCauseCandidate v2 字段（不暴露 adtributor_attribute 工具）。
3. net_gmv_chain 分解。
4. anomaly_injection 扩 20 case + ground truth；seed 幂等。
5. eval 扩 20 case（C19/C20 误报陷阱、C06/C07 多维断言）。
6. 发现型多元素/跨维证明来自 E2-family drilldown + ranker-internal Adtributor；
   首个 E3-family signal 后进入 E4，禁止逐元素 E3 fetch 消耗预算。

验收：20/20 intent 与 anomaly；top1≥80%、top3≥90%；C19/C20 零误报。

## P8（09）记忆 + 可观测 — codex/p8-memory-observability

1. 记忆四层（semantic seed 生成、episodic 写入、reflection 写入、case 冻结）。
2. memory retrieval eval（带/不带命中对照 + 零污染断言）。
3. UI 面板：Adtributor 候选、记忆分层、token/latency 看板。

验收：命中组正确率 ≥ 无命中组；memory_pollution_ok=100%；UI 测试
（injectable fake client）全绿。

## P9（10）Multi-Agent + 收尾 — codex/p9-multiagent-final

1. 分诊主 agent + gmv_family/rate_family experts（subagents,
   response_format=RunOutcome，预算 run 级共享）。
2. 开关差分测试：multi_agent on/off 判分字段一致。
3. 收尾：README、最终 eval 报告、文档一致性清扫。

验收：00-overview §4 全表门槛；连续 2 次 `make eval` 全绿。

## 测试迁移清单（P6 作废/重写）

| v1 测试 | 处置 |
|---|---|
| test_graph.py | 重写为 test_orchestrator.py（生命周期/终态化/repair 重入） |
| test_react.py | 重写为 test_middleware.py（args/预算/短路/持久化兜底） |
| test_zero_fallback.py | 全量重写（v2 八项负向场景） |
| test_reflection.py | 输入改 persisted artifacts，断言逻辑保留 |
| test_tools.py | 薄改（langchain @tool 包装 + In/Out schema） |
| 其余（guard/renderer/seed/api/eval/memory/…） | 不动，必须持续全绿 |
