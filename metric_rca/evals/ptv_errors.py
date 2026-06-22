"""Typed failures for Predict -> Test -> Verify orchestration."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class PtvErrorCode(StrEnum):
    CYCLE_ID_INVALID = "PTV_CYCLE_ID_INVALID"
    ROUND_NUMBER_INVALID = "PTV_ROUND_NUMBER_INVALID"
    ARTIFACT_MISSING = "PTV_ARTIFACT_MISSING"
    ARTIFACT_INVALID = "PTV_ARTIFACT_INVALID"
    JSON_INVALID = "PTV_JSON_INVALID"
    JSONL_INVALID = "PTV_JSONL_INVALID"
    COMMAND_INVALID = "PTV_COMMAND_INVALID"
    COMMAND_FAILED = "PTV_COMMAND_FAILED"
    PARALLEL_STAGE_FAILED = "PTV_PARALLEL_STAGE_FAILED"
    BARRIER_NOT_REACHED = "PTV_BARRIER_NOT_REACHED"
    EVAL_RESULT_MISSING = "PTV_EVAL_RESULT_MISSING"
    EVAL_RESULT_INVALID = "PTV_EVAL_RESULT_INVALID"
    PREDICTION_INVALID = "PTV_PREDICTION_INVALID"
    PREDICTION_INCOMPLETE = "PTV_PREDICTION_INCOMPLETE"
    PREDICTION_STALE = "PTV_STALE"
    PREDICTION_LEAKAGE = "PTV_FAKE"
    PREDICTION_TEMPLATE = "PTV_TEMPLATE"
    FIX_COMMIT_MISSING = "PTV_NOFIX"
    DIAGNOSIS_MISSING = "PTV_NODIAG"
    CONTROLLER_RULES_MISSING = "PTV_NORULES"
    FIX_CATEGORY_STALL = "PTV_STALL"
    COMMIT_INVALID = "PTV_COMMIT_INVALID"
    COMMIT_LINEAGE_INVALID = "PTV_COMMIT_LINEAGE_INVALID"
    ANALYST_OUTPUT_INVALID = "PTV_ANALYST_OUTPUT_INVALID"
    TWO_GREEN_INVALID = "PTV_TWO_GREEN_INVALID"
    GIT_COMMAND_FAILED = "PTV_GIT_COMMAND_FAILED"


class PtvRuntimeError(RuntimeError):
    """Fail-fast PTV error carrying a stable machine-readable code."""

    def __init__(
        self,
        code: PtvErrorCode | str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = message
        self.context = dict(context or {})
        super().__init__(f"{self.code}: {message}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code,
            "message": self.message,
            "context": self.context,
        }
