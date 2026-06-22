"""Declarative dimension catalog used by the scenario data compiler."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


REQUIRED_DIMENSIONS = (
    "channel",
    "campaign",
    "category",
    "product",
    "device",
    "geo",
    "shop",
    "brand",
    "warehouse",
    "logistics_provider",
    "payment_type",
    "membership_segment",
    "price_band",
    "promotion_type",
    "landing_page",
)


class DimensionCatalogError(ValueError):
    def __init__(self, code: str, message: str, *, context: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.context = dict(context or {})
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DimensionValue:
    value_id: str
    weight: float
    attributes: dict[str, Any]


@dataclass(frozen=True)
class DimensionCatalog:
    catalog_id: str
    dimensions: dict[str, tuple[DimensionValue, ...]]

    @classmethod
    def load(cls, path: Path) -> "DimensionCatalog":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DimensionCatalogError(
                "DIMENSION_CATALOG_INVALID",
                "catalog YAML must be JSON-compatible YAML",
                context={"path": str(path), "line": exc.lineno, "column": exc.colno},
            ) from exc
        if not isinstance(payload, dict):
            raise DimensionCatalogError("DIMENSION_CATALOG_INVALID", "catalog root must be an object")
        dimensions_payload = payload.get("dimensions")
        if not isinstance(dimensions_payload, dict):
            raise DimensionCatalogError("DIMENSION_CATALOG_INVALID", "catalog dimensions must be an object")
        dimensions: dict[str, tuple[DimensionValue, ...]] = {}
        for dimension, values_payload in dimensions_payload.items():
            if not isinstance(values_payload, list) or not values_payload:
                raise DimensionCatalogError(
                    "DIMENSION_CATALOG_INVALID",
                    "each dimension requires a non-empty value list",
                    context={"dimension": dimension},
                )
            values: list[DimensionValue] = []
            seen: set[str] = set()
            for row in values_payload:
                if not isinstance(row, dict):
                    raise DimensionCatalogError(
                        "DIMENSION_CATALOG_INVALID",
                        "dimension value must be an object",
                        context={"dimension": dimension},
                    )
                value_id = row.get("id")
                weight = row.get("weight", 1.0)
                attributes = row.get("attributes", {})
                if not isinstance(value_id, str) or not value_id:
                    raise DimensionCatalogError(
                        "DIMENSION_CATALOG_INVALID",
                        "dimension value id must be non-empty",
                        context={"dimension": dimension},
                    )
                if value_id in seen:
                    raise DimensionCatalogError(
                        "DIMENSION_CATALOG_INVALID",
                        "dimension value ids must be unique",
                        context={"dimension": dimension, "value_id": value_id},
                    )
                if not isinstance(weight, (int, float)) or isinstance(weight, bool) or float(weight) <= 0.0:
                    raise DimensionCatalogError(
                        "DIMENSION_CATALOG_INVALID",
                        "dimension value weight must be positive",
                        context={"dimension": dimension, "value_id": value_id},
                    )
                if not isinstance(attributes, dict):
                    raise DimensionCatalogError(
                        "DIMENSION_CATALOG_INVALID",
                        "dimension value attributes must be an object",
                        context={"dimension": dimension, "value_id": value_id},
                    )
                seen.add(value_id)
                values.append(DimensionValue(value_id=value_id, weight=float(weight), attributes=dict(attributes)))
            dimensions[str(dimension)] = tuple(values)
        catalog = cls(catalog_id=str(payload.get("catalog_id", path.stem)), dimensions=dimensions)
        catalog.validate()
        return catalog

    def validate(self) -> None:
        missing = sorted(set(REQUIRED_DIMENSIONS) - set(self.dimensions))
        if missing:
            raise DimensionCatalogError(
                "DIMENSION_COVERAGE_MISSING",
                "catalog does not cover every required business dimension",
                context={"missing": missing},
            )
        products = self.dimensions["product"]
        for product in products:
            missing_attributes = sorted({"category", "brand", "price_band", "base_price", "demand_factor"} - set(product.attributes))
            if missing_attributes:
                raise DimensionCatalogError(
                    "PRODUCT_CATALOG_INVALID",
                    "product attributes are incomplete",
                    context={"product": product.value_id, "missing": missing_attributes},
                )
            for relation in ("category", "brand", "price_band"):
                if str(product.attributes[relation]) not in self.value_ids(relation):
                    raise DimensionCatalogError(
                        "PRODUCT_CATALOG_INVALID",
                        "product relation references an unknown dimension value",
                        context={"product": product.value_id, "relation": relation, "value": product.attributes[relation]},
                    )

    def values(self, dimension: str) -> tuple[DimensionValue, ...]:
        try:
            return self.dimensions[dimension]
        except KeyError as exc:
            raise DimensionCatalogError(
                "DIMENSION_UNKNOWN",
                "dimension is not present in the catalog",
                context={"dimension": dimension},
            ) from exc

    def value_ids(self, dimension: str) -> tuple[str, ...]:
        return tuple(value.value_id for value in self.values(dimension))

    def value(self, dimension: str, value_id: str) -> DimensionValue:
        for value in self.values(dimension):
            if value.value_id == value_id:
                return value
        raise DimensionCatalogError(
            "DIMENSION_VALUE_UNKNOWN",
            "dimension value is not present in the catalog",
            context={"dimension": dimension, "value_id": value_id},
        )
