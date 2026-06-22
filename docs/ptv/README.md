# PTV — Predict → Test → Verify

PTV 是一套通用的 agent eval 优化协议。它的核心假设是：如果你能准确预测
系统行为，说明你理解了系统；如果预测失败，gap 精确指向需要修复的位置。

本目录分两层：

## Layer 1: 通用协议（可跨项目复用）

| File | Purpose |
|------|---------|
| [01-philosophy.md](01-philosophy.md) | PTV 的认知模型：预测是认知测试，不是答案抄写 |
| [02-workflow.md](02-workflow.md) | 与项目无关的 PTV 循环步骤和 Codex 调度规则 |
| [03-prediction-protocol.md](03-prediction-protocol.md) | 预测的通用 schema、硬规则、多轮迭代约束 |
| [04-diagnosis-protocol.md](04-diagnosis-protocol.md) | 诊断输出格式、分类框架、升级触发条件 |
| [05-grpo-bridge.md](05-grpo-bridge.md) | PTV 轨迹如何转化为 GRPO 训练数据的通用映射 |
| [06-enforcement.md](06-enforcement.md) | 反作弊检测规则和对抗性审查清单 |
| [07-known-issues.md](07-known-issues.md) | 已验证但留待后续优化的 PTV 缺陷记录 |

## Layer 2: 项目绑定（MetricRCA 特化）

| File | Purpose |
|------|---------|
| [bindings/metricrca.md](bindings/metricrca.md) | MetricRCA 的 fix taxonomy、eval 命令、scoring metrics、prediction aspects |

其他项目只需新增 `bindings/<project>.md`，协议层不动。

## Tooling

PTV 目前的工具实现在 MetricRCA 的 `metric_rca/evals/` 下：

```
prediction.py     — 预测 schema + 验证 CLI
gap_analyzer.py   — 预测 vs 实际的 aspect-aware 比较
grpo_dataset.py   — 轨迹导出 + binary judge reward
scorer.py         — 确定性评分指标
```

这些工具未来可以抽成独立包。当前先和项目同仓。
