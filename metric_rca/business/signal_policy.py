"""Deterministic signal-type policy for related-signal evidence."""

from __future__ import annotations

from metric_rca.business.policy_registry import (
    DEFAULT_POLICY_REGISTRY,
    DEFAULT_SIGNAL_POLICIES,
    AllowedDimensionsValidator,
    MetricPolicyRegistry,
    MetricSignalPolicy,
    SignalType,
    select_signal_type as _select_signal_type,
    select_signal_type_for_metric_dimension as _select_signal_type_for_metric_dimension,
)

SignalRule = MetricSignalPolicy
SIGNAL_RULES: tuple[SignalRule, ...] = DEFAULT_SIGNAL_POLICIES


def select_signal_type(
    *,
    metric_id: str,
    dimension: str,
    root_cause_type: str,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> SignalType:
    return _select_signal_type(
        metric_id=metric_id,
        dimension=dimension,
        root_cause_type=root_cause_type,
        registry=registry,
        validate_dimensions=validate_dimensions,
    )


def select_signal_type_for_metric_dimension(
    *,
    metric_id: str,
    dimension: str,
    registry: MetricPolicyRegistry = DEFAULT_POLICY_REGISTRY,
    validate_dimensions: AllowedDimensionsValidator | None = None,
) -> SignalType:
    return _select_signal_type_for_metric_dimension(
        metric_id=metric_id,
        dimension=dimension,
        registry=registry,
        validate_dimensions=validate_dimensions,
    )


__all__ = [
    "SIGNAL_RULES",
    "SignalRule",
    "SignalType",
    "select_signal_type",
    "select_signal_type_for_metric_dimension",
]
