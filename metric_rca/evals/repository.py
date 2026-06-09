"""Eval-only persistence helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


def read_ground_truth_cases(repository: Any, case_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not case_ids:
        return {}
    placeholders = ", ".join(f":case_id_{index}" for index, _ in enumerate(case_ids))
    params = {f"case_id_{index}": case_id for index, case_id in enumerate(case_ids)}
    try:
        with repository._audit_engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"""
                        SELECT case_id, business_date, metric_id, expected_anomaly,
                               root_cause_type, dimension, element
                        FROM anomaly_ground_truth
                        WHERE case_id IN ({placeholders})
                        """
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return {str(row["case_id"]): dict(row) for row in rows}
    except SQLAlchemyError as exc:
        raise RuntimeError("SYSTEM_TABLE_READ_FAILED") from exc
