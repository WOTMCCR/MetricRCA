from __future__ import annotations

from enum import Enum


class MetricId(str, Enum):
    GMV = "gmv"
    NET_GMV = "net_gmv"
    UV = "uv"
    PAY_CVR = "pay_cvr"
    AOV = "aov"
    REFUND_RATE = "refund_rate"
    STOCKOUT_RATE = "stockout_rate"
    COMPLAINT_RATE = "complaint_rate"
    CAMPAIGN_ROI = "campaign_roi"


class DimensionId(str, Enum):
    CHANNEL = "channel"
    CATEGORY = "category"
    DEVICE = "device"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"


class RootCauseType(str, Enum):
    CAMPAIGN_TRAFFIC_DROP = "campaign_traffic_drop"
    STOCKOUT = "stockout"
    CONVERSION_DROP = "conversion_drop"
    COMPLAINT_OR_QUALITY_ISSUE = "complaint_or_quality_issue"
    AOV_DROP = "aov_drop"
    NO_ANOMALY = "no_anomaly"


class EvidenceVerdict(str, Enum):
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    INSUFFICIENT = "insufficient"
    RULED_OUT = "ruled_out"
