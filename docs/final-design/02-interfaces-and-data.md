# 最终版接口与数据结构（v2 增量）

> 本文只列相对 MVP 的**增量与变更**；未提及的契约（QuerySpec、SQLPlan、
> Evidence、TraceStep、API 错误结构等）原样沿用 `docs/MetricRCA.md` §2/§9/§14。

## 1. 工具契约（deepagents 注册白名单）

每个工具一个 Pydantic In/Out（`extra="forbid"`），由 GuardMiddleware 校验。
工具实现是 v1 确定性 tools 的薄迁移，数据路径不变。

| 工具 | In（关键字段） | Out（关键字段） | 阶段 |
|---|---|---|---|
| detect_anomaly | metric_id, target_date | is_anomaly, z, delta_pct, baseline, evidence_id | P6（迁移） |
| drilldown_dimension | metric_id, dimension | contrib_by_element, evidence_id | P6（迁移） |
| fetch_related_signal | signal, filters | signal_summary, evidence_id | P6（迁移） |
| calculate_contribution | decompose_spec | factor_deltas, evidence_id | P6（迁移） |
| rank_root_causes | （无 args，读 run 内 persisted evidence） | candidates, evidence_id(E_rank) | P6（迁移）；P7 内部确定性调用 Adtributor |
| write_todos（deepagents 内置） | — | — | 保留，仅记录 |

LLM 只能看到 Out 的结构化摘要，永远看不到原始行集。

> **ADL-0009 决策（2026-06-13）**：Adtributor **不是** LLM 动作空间里的独立工具。
> 设计原意是「Adtributor 仅用于候选排序而非直接结论」，而排序是确定性的——因此
> Adtributor 落在 `rank_root_causes` 的确定性实现内部，适用时自动对已持久化的
> drilldown Evidence 运行 EP/Surprise，产出排序证据。**不引入 `adtributor_attribute`
> 工具**，从而消除「LLM 调完 Adtributor 就以为归因结束、停在 E_adt 不再走
> signal→contribution→rank」这一整类失败。LLM 的动作空间在 P7 保持与 P6 同构。

Evidence alias 约束：系统表 `evidence.evidence_id` 仍是 64 字符上限。P7 发现型
流程允许 E2/E3 family alias（如 `E2_category`、`E3_ch_paid_ads`、
`E3_cat_electronics`），eval run_id 生成需给 alias 预留长度。E3 的维度 token 使用
紧凑映射（channel→ch、category→cat、device→dev、product→prod、warehouse→wh），
长 element token 用确定性哈希截断；不得依赖 DB 写入失败来发现超长 id。

## 2. RootCauseCandidate v2

```python
class RootCauseCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_cause_type: str
    dimension: Optional[str] = None          # 兼容单维（v1）
    element: Optional[str] = None
    dimension_elements: list[tuple[str, str]] = Field(default_factory=list)
                                             # v2 多维组合 [(dim, element)]
    contribution_pct: float
    explanatory_power: Optional[float] = None   # Adtributor EP（v2）
    surprise_js: Optional[float] = None         # JS 散度 surprise（v2）
    signal_severity: float
    evidence_support: float
    reflection_factor: float = 1.0
    eng_confidence: float
    verdict: str
    evidence_ids: list[str] = Field(default_factory=list)
```

排序公式 v2：Adtributor 启用时 `contribution_score` 由 EP 替代，surprise 作为
跨候选 tie-breaker；其余因子与 v1 相同。单维路径（v1 公式）保留为
adtributor 不适用指标（比率类指标的分子分母分别跑 EP，见 §3）。

## 3. Adtributor 算法规格（services/adtributor_service.py，确定性）

来源：Bhagwan et al., NSDI '14。本系统改造：forecast F = 前 4 同星期几基线均值。

> **归位（ADL-0009）**：`adtributor_service` 是纯函数服务（无 DB / 无 repository
> import，须过 services-purity 测试）。它只吃 `rank_root_causes` 传入的、已由
> `drilldown_dimension` 持久化的 per-element actual/forecast（来自 Evidence
> result_summary），**绝不自取数、不读 fact 表、不读 anomaly_ground_truth、不接受
> 字面量喂值**。`rank_root_causes` 在适用指标/维度上确定性调用它，把 EP/surprise
> 写入候选并落入 E_rank；不适用时返回 `ADTRIBUTOR_NOT_APPLICABLE` 并退回单维路径。

- 可加指标（gmv / net_gmv / uv）：`EP_ij = (A_ij − F_ij) / (A − F)`。
- 比率指标（pay_cvr / refund_rate / stockout_rate / complaint_rate）：
  对分子、分母分别计算 EP 后按论文比率扩展取净效应；MVP 范围内允许退化为
  「分子分母分别归因 + 文字合成」，但必须在 result_summary 标注口径。
- Surprise：`p=F_ij/F, q=A_ij/A`，JS 散度公式照论文；p 或 q 为 0 时按公式
  自然有限，不做特殊兜底。
- 候选选择：单维内按 surprise 降序贪心，单元素 EP > T_EEP=0.1，
  累计 EP > T_EP=0.67 停；跨维取 surprise 最高的 top-3 维度。阈值入 Settings
  （`adtributor_t_ep` / `adtributor_t_eep`）。
- 输出仅作候选排序证据（E_rank，并同步 E4 selected/candidates 的 EP/surprise），结论仍需 Reflection evidence 校验。
- E3 只验证被选元素的一条相关信号；多元素/跨维组合由已持久化的 E2 drilldown
  per-element actual/forecast 进入 `rank_root_causes` 后计算。E4 前额外 E3 fetch
  会被 middleware 以 `E3_ALREADY_EXISTS` recoverable 拒绝。

## 4. 净 GMV 分解

`net_gmv = gmv − refund_amount`。calculate_contribution 的 decompose_spec 新增
`net_gmv_chain`：先算 gmv 与 refund 各自 delta 贡献，主导侧继续走
UV×CVR×AOV（gmv 侧）或退款维度下钻（refund 侧）。口径与 v1 §12 一致。

## 5. 记忆分层 v2（memory_record 表结构不变）

| layer | 写入时机 | 内容 | 读取影响 |
|---|---|---|---|
| semantic | seed 时由 metadata 生成；人工可补 | 指标别名、业务规则 | 仅辅助 intent 解析提示 |
| episodic | 每次 run 终态化 | case 摘要（metric/dim/root_cause/verdict） | 调整下钻优先级 |
| reflection | run failed 或 repair 发生时 | 失败教训（error_code、缺口） | 调整 expert prompt 提示 |
| case（legacy） | **冻结只读** | v1 历史记录 | 同 episodic，读兼容 |

污染控制不变：命中只调优先级；reflection_factor ≤ 1.2 且需当前 run 证据独立
复现；memory hit 永不作为 evidence_id（`memory_pollution_ok` eval 校验）。

## 6. DDL 变更（唯一）

```sql
ALTER TABLE trace_step ADD COLUMN token_usage JSON NULL;
```

seed 幂等重建即可，无迁移脚本需求。

## 7. Settings 增量

```python
multi_agent_enabled: bool = False
llm_provider: str | None = None   # 必填；openai 或 openai-compatible/deepseek
llm_model: str            # 必填，无默认——LLM 不可用必须显式失败
llm_base_url: str | None = None   # OpenAI-compatible provider 必填；原生 OpenAI 可为空
llm_structured_output_method: Literal["json_schema","json_mode","function_calling"] = "json_schema"
llm_temperature: float = 0.0
adtributor_t_ep: float = 0.67
adtributor_t_eep: float = 0.10
```

> **Provider compatibility（ADL-0011）**：`MetricService` 和 deepagents factory
> 共用 OpenAI-compatible ChatModel 构造边界。切换 DeepSeek / 私有 OpenAI-compatible
> 网关 / 其他兼容 endpoint 只允许通过 `METRIC_RCA_LLM_PROVIDER`、
> `METRIC_RCA_LLM_MODEL`、`METRIC_RCA_LLM_API_KEY`、`METRIC_RCA_LLM_BASE_URL`
> 和 `METRIC_RCA_LLM_STRUCTURED_OUTPUT_METHOD` 完成。兼容 provider 不得静默读取
> `OPENAI_API_KEY` 作为第三方 key；缺少 key/base_url 必须 typed fail-fast。

> **模型策略（ADL-0009 → ADL-0012 修订）**：eval 必须用具备稳健指令遵循能力的
> 模型。可接受：GPT-5 家族（含 Nano）、GPT-4.1（非 mini）、DeepSeek-V3 等同级模型。
> **不接受已知弱模型**（gpt-4.1-mini、gpt-3.5-turbo 等）。不再用硬编码黑名单拦截；
> 由 eval 结果 + 审查人工判定模型是否足够。Makefile `eval` 目标须显式传
> `METRIC_RCA_LLM_PROVIDER` / `METRIC_RCA_LLM_MODEL` / `METRIC_RCA_LLM_API_KEY`。
> 验收用的 provider/model 记入 eval_run.summary 以便审计。守卫是纵深防御，不得用来
> 补偿弱模型的解析能力。
>
> **Per-request LLM 选择（ADL-0012，P8 范围）**：`RunCreateRequest` 将新增可选字段
> `llm_provider`/`llm_model`/`llm_api_key`，传入时覆盖 Settings 默认值（作用域
> 仅限该 run）。eval HTTP 客户端利用此机制在同一后端实例上同时对比多 provider 结果。

## 8. 错误码增量

| 错误码 | 场景 | recoverable |
|---|---|---|
| BUDGET_EXCEEDED | 首次预算耗尽时提示只能 rank/结束；预算耗尽后再次调用 data-fetching 工具则 run failed | 首次是；重复越权否 |
| NO_ANOMALY_CONTRACT_VIOLATED | 无异常却出现下钻/rank/任务 | 否 |
| E3_ALREADY_EXISTS | E4 前已有 E3-family signal，阻止额外 fetch 并引导 calculate_contribution | 是 |
| E4_ALREADY_EXISTS | 当前 run 已有 E4，阻止不同选择覆盖并引导 rank_root_causes | 是 |
| ADTRIBUTOR_NOT_APPLICABLE | 指标/维度不支持 EP 口径 | 是（换单维路径） |

其余错误码表沿用 §18，`LLM_REQUIRED_UNAVAILABLE` 现在适用于所有 run。

## 9. 20-case 异常库（data/anomaly_injection.py + ground truth）

保留 MVP 5 case（C01–C05），新增 15 个。注入日均为 `2026-06-05`，
no-anomaly 类用 `2026-06-04`，固定 SEED 幂等。

| case_id | 指标 | 注入方式 | 期望主因（ground truth） |
|---|---|---|---|
| C01 gmv_paid_ads_drop | gmv↓ | paid_ads spend/clicks/uv 骤降 | campaign_traffic_drop |
| C02 gmv_stockout_electronics | gmv↓ | electronics stockout_hours↑ | stockout |
| C03 cvr_mobile_drop | pay_cvr↓ | mobile pay_user_cnt 骤降 | conversion_drop |
| C04 refund_rate_product_quality | refund_rate↑ | 单商品投诉/退款激增 | complaint_or_quality_issue |
| C05 gmv_no_anomaly | gmv | 2026-06-04 无注入 | no_anomaly |
| C06 gmv_multi_channel_drop | gmv↓ | paid_ads+social 同步降 | campaign_traffic_drop（多元素） |
| C07 gmv_category_channel_cross | gmv↓ | electronics×paid_ads 交叉降 | campaign_traffic_drop（多维组合） |
| C08 gmv_aov_drop | gmv↓ | 高价 SKU 销量占比骤降 | aov_drop |
| C09 gmv_uv_organic_drop | gmv↓ | organic uv 骤降（spend 正常） | campaign_traffic_drop（uv 侧） |
| C10 gmv_price_change | gmv↓ | 类目级降价（量平价跌） | aov_drop |
| C11 gmv_promo_end_falloff | gmv↓ | 前 7 天 promo 抬高、当日回落 | campaign_traffic_drop |
| C12 gmv_single_sku_stockout | gmv↓ | 爆款 SKU 全仓缺货 | stockout |
| C13 net_gmv_refund_spike | net_gmv↓ | gmv 平、refund_amount 激增 | complaint_or_quality_issue |
| C14 net_gmv_gmv_driven | net_gmv↓ | refund 平、gmv 下降（渠道） | campaign_traffic_drop |
| C15 refund_rate_logistics | refund_rate↑ | logistics ticket + 退款（多商品） | complaint_or_quality_issue |
| C16 stockout_rate_warehouse | stockout_rate↑ | 单仓 stockout_hours 激增 | stockout |
| C17 complaint_rate_quality | complaint_rate↑ | quality ticket 激增（单类目） | complaint_or_quality_issue |
| C18 cvr_channel_landing | pay_cvr↓ | 单渠道 add_cart→pay 断崖 | conversion_drop |
| C19 gmv_seasonal_false_positive | gmv | 周末效应（基线同为周末，不应报） | no_anomaly |
| C20 cvr_no_anomaly_noise | pay_cvr | 仅噪声波动（<阈值） | no_anomaly |

要求：C19/C20 是**误报陷阱**，判有异常即 fail；C06/C07 验证 Adtributor 多元素/
多维能力；每 case 在 `anomaly_ground_truth` 落 dimension/element 字段。

### 9.1 eval 题面完整性铁律（ADL-0009，不可违反）

eval 的价值在于测「自然业务问句 → RCA」。**问题文本不得编码答案**：

- **禁止** `metric_id=<x>` 字面语法写进 question；目标 KPI 用自然语言表达
  （如「昨天 GMV 为什么下降？」「退款率为什么上升？」）。`intent-parse accuracy`
  必须是真实可测的 LLM 解析指标，不能因题面给出 metric 而恒为 1.0。
- **禁止** 把根因机制写进 question（如 `from stockout` / `because refunds increased`
  / `from UV` / `after a price change`）。机制（root_cause_type）是系统须**从证据
  推断**的目标，不能在题面预答。
- 维度/元素：**仅当**该 case 的真实场景就是「用户指定切片」时，问题才可自然点名
  维度值（如「为什么 paid_ads 渠道 GMV 下降？」），此时被评分的是 root_cause_type
  机制而非维度本身；**发现型 case（C06/C07/C08/C09 等）不得在题面给出待发现的
  维度/元素**，否则下钻被预答、归因被架空。

**target metric vs cause mechanism**：intent / expert system prompt 必须显式区分
——target metric 是「被解释的 KPI（题面问的那个指标）」，stockout/refund/UV/AOV 等
是**待验证的假设机制**，永不改写 target metric。这是修复指标漂移的正确手段，替代
把答案写进题面。

### 9.2 C07 多维必须被证明（ADL-0009）

C07 注入须产生**占主导的 electronics×paid_ads 交叉信号**；最终 selected_candidate
的 `dimension_elements` 必须同时包含 `(channel, paid_ads)` 与 `(category, electronics)`，
eval 断言多维组合成立。**不得**为求绿把 C07 真值塌缩为单维 `channel=paid_ads`——
那是掩盖 P7 多维归因能力缺口。若注入无法稳定产生主导交叉，是 seed/算法问题，修
系统，不改真值。
