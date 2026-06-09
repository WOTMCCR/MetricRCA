"""Deprecated eval persistence helpers.

Eval ground truth reads are owned by MetricRepository.get_ground_truth_cases.
This module remains as a fail-fast compatibility boundary so old imports cannot
silently regain private repository field access.
"""

from __future__ import annotations

from typing import Any


def read_ground_truth_cases(repository: Any, case_ids: list[str]) -> dict[str, dict[str, Any]]:
    raise RuntimeError("EVAL_GROUND_TRUTH_MISSING: use MetricRepository.get_ground_truth_cases")
