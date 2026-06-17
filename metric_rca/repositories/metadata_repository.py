"""DB-backed metadata repository for metric definitions and schema context."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from metric_rca.config.settings import Settings
from metric_rca.domain.models import MetricDefinition
from metric_rca.services.metric_contracts import MetricServiceError


_DIMENSION_VALUE_SQL: dict[str, str] = {
    "channel": """
        SELECT DISTINCT channel AS value FROM fact_order
        UNION
        SELECT DISTINCT channel AS value FROM fact_traffic
        UNION
        SELECT DISTINCT channel AS value FROM fact_campaign
        ORDER BY value
    """,
    "category": """
        SELECT DISTINCT category AS value
        FROM dim_product
        ORDER BY value
    """,
    "device": """
        SELECT DISTINCT device AS value FROM fact_order
        UNION
        SELECT DISTINCT device AS value FROM fact_traffic
        ORDER BY value
    """,
    "product": """
        SELECT DISTINCT CAST(product_id AS CHAR) AS value
        FROM dim_product
        ORDER BY value
    """,
    "warehouse": """
        SELECT DISTINCT warehouse AS value
        FROM fact_inventory
        ORDER BY value
    """,
}


class MetadataRepository:
    """Reads metadata tables and schema-derived values from the application DB."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def from_settings(cls, settings: Settings) -> MetadataRepository:
        return cls(create_engine(str(settings.db_dsn), pool_pre_ping=True))

    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        row = self._metric_row(metric_id)
        if row is None:
            raise MetricServiceError("METRIC_NOT_FOUND", f"metric not found: {metric_id}")
        return self._definition_from_row(row)

    def get_schema_context(self, metric_id: str) -> dict[str, object]:
        try:
            definition = self.get_metric_definition(metric_id)
        except MetricServiceError as exc:
            if exc.code == "METRIC_NOT_FOUND":
                raise MetricServiceError(
                    "SCHEMA_CONTEXT_MISSING", f"schema context missing: {metric_id}"
                ) from exc
            raise
        if not definition.source_table or not definition.allowed_dimensions:
            raise MetricServiceError("SCHEMA_CONTEXT_MISSING", f"schema context missing: {metric_id}")
        return {
            "source_table": definition.source_table,
            "allowed_dimensions": definition.allowed_dimensions,
            "formula": definition.formula,
            "metric_family": definition.metric_family,
        }

    def list_metrics(self) -> list[MetricDefinition]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT metric_id, display_name, formula, metric_family, numerator_sql_fragment,
                               denominator_sql_fragment, higher_is_better, source_table,
                               allowed_dimensions
                        FROM metric_definition
                        ORDER BY metric_id
                        """
                    )
                )
                .mappings()
                .all()
            )
        return [self._definition_from_row(dict(row)) for row in rows]

    def list_dimension_values(self, dimension: str) -> list[str]:
        sql = _DIMENSION_VALUE_SQL.get(dimension)
        if sql is None:
            raise MetricServiceError("DIMENSION_NOT_ALLOWED", f"dimension not allowed: {dimension}")
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql)).mappings().all()
        return [str(row["value"]) for row in rows if row["value"] is not None]

    def _metric_row(self, metric_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        """
                        SELECT metric_id, display_name, formula, metric_family, numerator_sql_fragment,
                               denominator_sql_fragment, higher_is_better, source_table,
                               allowed_dimensions
                        FROM metric_definition
                        WHERE metric_id = :metric_id
                        LIMIT 1
                        """
                    ),
                    {"metric_id": metric_id},
                )
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    def _definition_from_row(self, row: dict[str, Any]) -> MetricDefinition:
        try:
            allowed_dimensions = json.loads(row["allowed_dimensions"])
        except json.JSONDecodeError as exc:
            raise MetricServiceError(
                "SCHEMA_CONTEXT_MISSING",
                f"allowed_dimensions is invalid JSON for metric: {row['metric_id']}",
            ) from exc
        if not isinstance(allowed_dimensions, list) or not all(
            isinstance(value, str) for value in allowed_dimensions
        ):
            raise MetricServiceError(
                "SCHEMA_CONTEXT_MISSING",
                f"allowed_dimensions is invalid for metric: {row['metric_id']}",
            )
        return MetricDefinition(
            metric_id=row["metric_id"],
            display_name=row["display_name"],
            formula=row["formula"],
            metric_family=row["metric_family"],
            numerator_sql_fragment=row["numerator_sql_fragment"],
            denominator_sql_fragment=row["denominator_sql_fragment"],
            higher_is_better=bool(row["higher_is_better"]),
            allowed_dimensions=allowed_dimensions,
            source_table=row["source_table"],
        )
