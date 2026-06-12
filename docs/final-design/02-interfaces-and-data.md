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
| rank_root_causes | （无 args，读 run 内 persisted evidence） | candidates, evidence_id(E_rank) | P6（迁移；排序仍确定性） |
| adtributor_attribute | metric_id, dimensions(≤3) | ranked_dim_elements(EP, surprise), evidence_id | P7（新） |
| write_todos（deepagents 内置） | — | — | 保留，仅记录 |

LLM 只能看到 Out 的结构化摘要，永远看不到原始行集。

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

- 可加指标（gmv / net_gmv / uv）：`EP_ij = (A_ij − F_ij) / (A − F)`。
- 比率指标（pay_cvr / refund_rate / stockout_rate / complaint_rate）：
  对分子、分母分别计算 EP 后按论文比率扩展取净效应；MVP 范围内允许退化为
  「分子分母分别归因 + 文字合成」，但必须在 result_summary 标注口径。
- Surprise：`p=F_ij/F, q=A_ij/A`，JS 散度公式照论文；p 或 q 为 0 时按公式
  自然有限，不做特殊兜底。
- 候选选择：单维内按 surprise 降序贪心，单元素 EP > T_EEP=0.1，
  累计 EP > T_EP=0.67 停；跨维取 surprise 最高的 top-3 维度。阈值入 Settings
  （`adtributor_t_ep` / `adtributor_t_eep`）。
- 输出仅作候选排序证据（E_adt），结论仍需 Reflection evidence 校验。

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
llm_model: str            # 必填，无默认——LLM 不可用必须显式失败
llm_temperature: float = 0.0
adtributor_t_ep: float = 0.67
adtributor_t_eep: float = 0.10
```

## 8. 错误码增量

| 错误码 | 场景 | recoverable |
|---|---|---|
| BUDGET_EXCEEDED | middleware 预算硬中断后 LLM 仍越权 | 否（run failed） |
| NO_ANOMALY_CONTRACT_VIOLATED | 无异常却出现下钻/rank/任务 | 否 |
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
