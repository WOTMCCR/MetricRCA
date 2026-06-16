# 阶段 B — Eval-Driven System Optimization via PTV

> 状态：accepted（2026-06-16 用户批准）。本文档是阶段 B 的设计源（design source of truth），
> 与 final-design 中已描述的不变量冲突时以 01-architecture 为准；本文档只定义
> **eval 失败驱动的修复约束**。

## B-0. 目标

在阶段 A 构建的 28-case eval harness 上运行 `make eval`，用 PTV（Predict-Then-Verify）
流程系统性地定位失败根因，然后以最小改动修复系统能力缺口，直到 28/28 连续 2 次 green。

**核心原则**：eval harness 是不动的标尺，系统是被修的对象。不准降标准、不准改题面、
不准弱化 scorer、不准调阈值迁就。

## B-1. PTV 流程规范

PTV 是"先预测、再观测、用 gap 驱动修复"的科学实验循环。每轮修复都走完整循环：

```
┌──────────────────────────────────────────────────────┐
│ PREDICT                                              │
│  对 28 个 case 写 5-aspect predictions               │
│  (intent / execution / evidence / memory / outcome)  │
│  → eval_out/{eval_id}/predictions.jsonl              │
├──────────────────────────────────────────────────────┤
│ EXECUTE                                              │
│  make eval-stream EVAL_ID={eval_id}                  │
│  → eval_out/{eval_id}.json + per-case artifacts      │
├──────────────────────────────────────────────────────┤
│ VERIFY                                               │
│  make eval-gaps EVAL_ID={eval_id}                    │
│  → gap_report.json + markdown                        │
│  divergence 类型:                                     │
│    correct       — 预测与实际一致                      │
│    design_flaw   — 系统设计缺陷（intent/guard/tool）   │
│    complexity_gap — 系统能力不足（LLM 规划弱）          │
│    overfit       — 预测过于悲观（系统比预期更好）        │
├──────────────────────────────────────────────────────┤
│ DIAGNOSE                                             │
│  对每个 design_flaw / complexity_gap:                 │
│    读 trace_steps → 定位失败的确切 tool/step           │
│    分类为 4 种修复类型之一（见 B-2）                    │
│    写入 fix 矩阵                                      │
├──────────────────────────────────────────────────────┤
│ FIX                                                  │
│  按修复优先级实施最小改动                               │
│  make test → make eval → 下一轮 PTV                   │
└──────────────────────────────────────────────────────┘
```

### PTV prediction 规范（每 case 5 个 aspect）

| Aspect | 必填 prediction key | 含义 |
|--------|---------------------|------|
| **intent** | `metric_id` | 预测 intent_planner 会把 question 解析为哪个 metric_id |
| **execution** | `tool_sequence` 或 `step_count` 或 `critical_decisions` + (no_anomaly case 必须含 `forbidden_tools`) | 预测 agent 的工具调用序列 |
| **evidence** | `chain` | 预测证据链是否完整（E1→E2→E3→E4→E_rank） |
| **memory** | `influence` | 预测 memory 对本次 run 的影响 |
| **outcome** | `root_cause_type`, `top1_ok`, `anomaly_ok` | 预测最终归因结果 |

每个 prediction 必须含 `reasoning`（为什么这么预测）和 `risks`（至少一条风险）。

## B-2. 修复类型分类（Fix Taxonomy）

| 类型 | 代号 | 改动范围 | 示例 |
|------|------|---------|------|
| **Intent Fix** | `FIX-I` | intent_planner prompt（parse_question system prompt） | "traffic" → uv 的自然语言别名 |
| **Guard Fix** | `FIX-G` | middleware.py 或 runner.py 的 guard 逻辑 | 异常检测方向性修复 |
| **Tool Fix** | `FIX-T` | tools/ 或 services/ 的工具实现 | detect_anomaly 正向检测 |
| **Prompt Fix** | `FIX-P` | expert prompt guidance | rate discovery 维度优先级 |

分类规则：
- intent_ok=0 → FIX-I
- intent_ok=1, anomaly_ok=0 → FIX-T 或 FIX-G
- intent_ok=1, anomaly_ok=1, top1_ok=0 → FIX-P 或 FIX-T

## B-3. 架构约束（修复红线）

所有修复必须遵守 01-architecture 的不变量：

| 约束 | 描述 |
|------|------|
| **数据路径不变** | QuerySpec → SQLRenderer → SQLGuard → Repository 是唯一取数路径 |
| **元数据路径不变** | MetricService 从 DB 读 metric_definition；不准在 prompt 或 service 硬编码映射 |
| **零静默兜底** | 所有失败路径必须产出 typed error_code |
| **report 是投影** | final report 来自 persisted Evidence 的机械投影 |
| **eval integrity** | 不准改 cases.jsonl、ground_truth、scorer、anomaly_injection |
| **LLM-first** | 自然语言语义解析只在 LLM intent_planner 中发生；middleware 不做 keyword parsing |
| **Backward compatible** | 原 20 case 必须持续 green |

### 预期 gap 的允许/禁止修复路径

| 预期 Gap | 允许 | 禁止 |
|----------|------|------|
| "traffic" → uv 映射失败 | FIX-I: intent prompt 别名指导 | Python keyword → metric_id 映射表 |
| "sales" → gmv 映射失败 | FIX-I: intent prompt 别名指导 | 同上 |
| 相对日期解析失败 | FIX-I: intent prompt 日期指导 | runner.py 中写日期 regex |
| 正向异常检测失败 | FIX-T: anomaly service abs(z) > z_thresh | 仅在 prompt 中告诉 LLM |
| Rate discovery 路径错误 | FIX-P: expert prompt discovery guidance | middleware 硬编码路由 |
| Composite dimension 不全 | FIX-P: discovery prompt 加强 | — |

## B-4. 验收门槛

| 指标 | 门槛 |
|------|------|
| intent-parse accuracy | 28/28 |
| anomaly-detection accuracy | 28/28（含 4 no_anomaly + 1 正向异常） |
| root-cause top-1 | ≥ 85% (≥24/28) |
| root-cause top-3 | ≥ 93% (≥26/28) |
| SQL safety | 100% |
| report_traceable_ok | 100% |
| memory_pollution_ok | 100% |
| no_anomaly_correct | 100%（4 trap 全 clean） |
| dangerous_sql_blocked | true |
| 原 20 case 回归 | 20/20 每轮 green |
| 连续 2 次 eval | green |

top-1 从 P9 的 100%（20 case）放宽到 85% 的原因：C27 (composite) 和 C28 (multi-day) 是
结构性难 case，允许少量 top-1 miss 但要求 top-3 cover。

## B-5. 与 final-design 的关系

阶段 B 不引入新的架构组件。所有修复发生在 prompt 层（intent/expert guidance）和
service 层（anomaly detection direction），不改变编排、守卫、工具契约或数据路径。

| 项 | final-design 定义 | 阶段 B 增量 |
|----|------------------|------------|
| 验收门槛 | 00-overview §4: 20 case | 扩展到 28 case |
| 架构约束 | 01-architecture §3 | 不变 |
| 工具契约 | 02-interfaces §1 | 不变，不新增工具 |
| 流程 | 03-flows §1-§5 | 不变 |
| PTV 工具 | prediction.py + gap_analyzer.py | P8 建成，直接复用 |
