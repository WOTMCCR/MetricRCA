"""Compatibility import for the old agent tool package during runtime migration."""

from metric_rca.business.signal_policy import (
    SIGNAL_RULES,
    SignalRule,
    SignalType,
    select_signal_type,
    select_signal_type_for_metric_dimension,
)

__all__ = [
    "SIGNAL_RULES",
    "SignalRule",
    "SignalType",
    "select_signal_type",
    "select_signal_type_for_metric_dimension",
]
