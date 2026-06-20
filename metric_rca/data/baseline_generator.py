"""Deterministic wide-row baseline generator for declared business dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import math
from typing import Any, Iterable

from metric_rca.data.dimension_catalog import DimensionCatalog, DimensionValue
from metric_rca.data.scenario_spec import ScenarioSpec


@dataclass(frozen=True)
class BaselineConfig:
    seed: int
    start_date: date
    end_date: date

    @classmethod
    def from_scenarios(cls, *, seed: int, scenarios: Iterable[ScenarioSpec]) -> "BaselineConfig":
        rows = tuple(scenarios)
        if not rows:
            raise ValueError("BASELINE_SCENARIOS_MISSING")
        earliest = min(min(scenario.relevant_dates()) for scenario in rows)
        latest = max(max(scenario.relevant_dates()) for scenario in rows)
        return cls(seed=seed, start_date=earliest, end_date=latest)


class BaselineGenerator:
    """Generate one row per date x channel x product x device.

    Secondary dimensions are selected by a stable weighted hash so the dataset
    covers them without constructing an intractable Cartesian product.
    """

    def __init__(self, *, catalog: DimensionCatalog, config: BaselineConfig) -> None:
        self._catalog = catalog
        self._config = config

    def generate(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current = self._config.start_date
        while current <= self._config.end_date:
            for channel in self._catalog.values("channel"):
                campaigns = self._campaigns_for_channel(channel.value_id)
                for product in self._catalog.values("product"):
                    for device in self._catalog.values("device"):
                        rows.append(
                            self._row(
                                business_date=current,
                                channel=channel,
                                campaign=self._choose(
                                    campaigns,
                                    key=f"campaign|{channel.value_id}|{product.value_id}|{device.value_id}",
                                ),
                                product=product,
                                device=device,
                            )
                        )
            current += timedelta(days=1)
        return rows

    def _row(
        self,
        *,
        business_date: date,
        channel: DimensionValue,
        campaign: DimensionValue,
        product: DimensionValue,
        device: DimensionValue,
    ) -> dict[str, Any]:
        entity_key = f"{channel.value_id}|{campaign.value_id}|{product.value_id}|{device.value_id}"
        key = f"{business_date}|{entity_key}"
        category = self._catalog.value("category", str(product.attributes["category"]))
        brand = self._catalog.value("brand", str(product.attributes["brand"]))
        price_band = self._catalog.value("price_band", str(product.attributes["price_band"]))
        geo = self._choose(self._catalog.values("geo"), key=f"geo|{entity_key}")
        shop = self._choose(self._catalog.values("shop"), key=f"shop|{entity_key}")
        warehouse = self._choose(self._catalog.values("warehouse"), key=f"warehouse|{entity_key}")
        logistics = self._choose(self._catalog.values("logistics_provider"), key=f"logistics|{entity_key}")
        payment = self._choose(self._catalog.values("payment_type"), key=f"payment|{entity_key}")
        membership = self._choose(self._catalog.values("membership_segment"), key=f"membership|{entity_key}")
        promotion = self._choose(self._catalog.values("promotion_type"), key=f"promotion|{entity_key}")
        landing_page = self._choose(self._catalog.values("landing_page"), key=f"landing|{entity_key}")

        weekday_factor = 0.96 + business_date.weekday() * 0.012
        noise = 0.96 + 0.08 * self._unit(f"noise|{key}")
        traffic_factor = self._number(channel, "traffic_factor", 1.0)
        demand_factor = self._number(product, "demand_factor", 1.0)
        category_factor = self._number(category, "demand_factor", 1.0)
        geo_factor = self._number(geo, "demand_factor", 1.0)
        shop_factor = self._number(shop, "demand_factor", 1.0)
        membership_factor = self._number(membership, "demand_factor", 1.0)
        promotion_demand = self._number(promotion, "demand_factor", 1.0)
        base_uv = 115.0 * traffic_factor * demand_factor * category_factor * geo_factor * shop_factor
        uv = max(1.0, base_uv * membership_factor * promotion_demand * weekday_factor * noise)
        sessions = uv * (0.89 + 0.06 * self._unit(f"sessions|{key}"))

        cvr = (
            0.042
            * self._number(channel, "cvr_factor", 1.0)
            * self._number(campaign, "quality_factor", 1.0)
            * self._number(device, "cvr_factor", 1.0)
            * self._number(landing_page, "cvr_factor", 1.0)
            * self._number(payment, "cvr_factor", 1.0)
            * self._number(membership, "cvr_factor", 1.0)
        )
        cvr *= 0.97 + 0.06 * self._unit(f"cvr|{key}")
        cvr = min(0.35, max(0.003, cvr))
        orders = min(sessions, max(0.0, sessions * cvr))

        base_price = float(product.attributes["base_price"])
        price_noise = 0.98 + 0.04 * self._unit(f"price|{key}")
        unit_price = base_price * price_noise
        promotion_discount = self._number(promotion, "discount_rate", 0.0)
        gross_merchandise_value = orders * unit_price * (1.0 - promotion_discount)
        refund_rate = (
            self._number(category, "refund_rate", 0.04)
            * self._number(logistics, "refund_factor", 1.0)
            * self._number(payment, "refund_factor", 1.0)
        )
        refund_rate *= 0.9 + 0.2 * self._unit(f"refund|{key}")
        refund_amount = gross_merchandise_value * min(0.45, max(0.0, refund_rate))

        stockout_hours = max(
            0.0,
            1.2
            * self._number(warehouse, "stockout_factor", 1.0)
            * self._number(category, "stockout_factor", 1.0)
            * (0.6 + 0.8 * self._unit(f"stockout|{key}")),
        )
        complaints = orders * self._number(product, "complaint_rate", 0.008) * (
            0.8 + 0.4 * self._unit(f"complaints|{key}")
        )
        delivery_delay_hours = max(
            0.0,
            self._number(logistics, "delay_hours", 8.0)
            * self._number(geo, "delivery_factor", 1.0)
            * (0.8 + 0.4 * self._unit(f"delay|{key}")),
        )
        spend = uv * self._number(channel, "spend_per_uv", 0.12) * self._number(campaign, "spend_factor", 1.0)
        impressions = uv * self._number(campaign, "impressions_per_uv", 8.0)
        clicks = min(impressions, uv * self._number(campaign, "clicks_per_uv", 1.3))

        row = {
            "row_id": sha256(f"{self._config.seed}|{key}".encode("utf-8")).hexdigest()[:24],
            "business_date": business_date.isoformat(),
            "channel": channel.value_id,
            "campaign": campaign.value_id,
            "category": category.value_id,
            "product": product.value_id,
            "device": device.value_id,
            "geo": geo.value_id,
            "shop": shop.value_id,
            "brand": brand.value_id,
            "warehouse": warehouse.value_id,
            "logistics_provider": logistics.value_id,
            "payment_type": payment.value_id,
            "membership_segment": membership.value_id,
            "price_band": price_band.value_id,
            "promotion_type": promotion.value_id,
            "landing_page": landing_page.value_id,
            "uv": round(uv, 6),
            "sessions": round(sessions, 6),
            "orders": round(orders, 6),
            "unit_price": round(unit_price, 6),
            "promotion_discount": round(promotion_discount, 6),
            "refund_amount": round(refund_amount, 6),
            "stockout_hours": round(stockout_hours, 6),
            "complaints": round(complaints, 6),
            "delivery_delay_hours": round(delivery_delay_hours, 6),
            "spend": round(spend, 6),
            "clicks": round(clicks, 6),
            "impressions": round(impressions, 6),
            "_applied_shocks": [],
        }
        return row

    def _campaigns_for_channel(self, channel: str) -> tuple[DimensionValue, ...]:
        matches = tuple(
            campaign
            for campaign in self._catalog.values("campaign")
            if str(campaign.attributes.get("channel")) == channel
        )
        if not matches:
            raise ValueError(f"CAMPAIGN_CHANNEL_COVERAGE_MISSING: {channel}")
        return matches

    def _choose(self, values: tuple[DimensionValue, ...], *, key: str) -> DimensionValue:
        total = sum(value.weight for value in values)
        position = self._unit(key) * total
        cumulative = 0.0
        for value in values:
            cumulative += value.weight
            if position <= cumulative:
                return value
        return values[-1]

    def _unit(self, key: str) -> float:
        digest = sha256(f"{self._config.seed}|{key}".encode("utf-8")).digest()
        integer = int.from_bytes(digest[:8], "big")
        return integer / float(2**64 - 1)

    @staticmethod
    def _number(value: DimensionValue, key: str, default: float) -> float:
        raw = value.attributes.get(key, default)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(float(raw)):
            raise ValueError(f"DIMENSION_NUMERIC_ATTRIBUTE_INVALID: {value.value_id}.{key}")
        return float(raw)
