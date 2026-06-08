"""领域枚举：系统中所有"封闭取值集合"的唯一来源。

为什么用 `str + Enum`：既能像字符串一样直接和 DB / JSON / API 交互，又能在代码里获得
成员约束与 IDE 补全。指标 / 维度 / 根因 / 证据判定这四类取值是受控动作空间与白名单校验的
基础——QuerySpec、SQLRenderer、归因、Reflection 都以它们为准绳，避免出现"魔法字符串"。

对应设计文档 docs/MetricRCA.md §2（domain/enums.py）。
"""

from __future__ import annotations

from enum import Enum


class MetricId(str, Enum):
    """可被诊断的指标白名单。

    GMV / NET_GMV / UV / PAY_CVR / AOV / REFUND_RATE / STOCKOUT_RATE / COMPLAINT_RATE
    是 MVP 指标；CAMPAIGN_ROI 标注为 1 个月增强项（Phase 1 的 QuerySpec 会把它排除）。
    """

    GMV = "gmv"
    NET_GMV = "net_gmv"
    UV = "uv"
    PAY_CVR = "pay_cvr"
    AOV = "aov"
    REFUND_RATE = "refund_rate"
    STOCKOUT_RATE = "stockout_rate"
    COMPLAINT_RATE = "complaint_rate"
    CAMPAIGN_ROI = "campaign_roi"  # 1 个月增强，非 MVP 取数指标


class DimensionId(str, Enum):
    """允许下钻 / 过滤的维度白名单（任意维度组合搜索不在 MVP 范围内）。"""

    CHANNEL = "channel"
    CATEGORY = "category"
    DEVICE = "device"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"


class RootCauseType(str, Enum):
    """归因输出的根因类型封闭集合。

    NO_ANOMALY 是显式成功分支（无异常），不能被当成"归因覆盖不足"的失败。
    """

    CAMPAIGN_TRAFFIC_DROP = "campaign_traffic_drop"
    STOCKOUT = "stockout"
    CONVERSION_DROP = "conversion_drop"
    COMPLAINT_OR_QUALITY_ISSUE = "complaint_or_quality_issue"
    AOV_DROP = "aov_drop"
    NO_ANOMALY = "no_anomaly"


class EvidenceVerdict(str, Enum):
    """单个根因候选的证据判定等级（供 Reflection 与报告措辞使用）。

    CONFIRMED=证据确认，LIKELY=可能贡献，INSUFFICIENT=证据不足，RULED_OUT=已排除。
    "导致"这类绝对因果措辞要求等级达到 CONFIRMED，避免把相关写成因果。
    """

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    INSUFFICIENT = "insufficient"
    RULED_OUT = "ruled_out"
