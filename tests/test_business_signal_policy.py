from __future__ import annotations

import pytest

from metric_rca.business.signal_policy import (
    select_signal_type,
    select_signal_type_for_metric_dimension,
)


def test_signal_policy_selects_related_signal_for_metric_dimension() -> None:
    assert select_signal_type_for_metric_dimension(metric_id="uv", dimension="channel") == "campaign"
    assert select_signal_type_for_metric_dimension(metric_id="refund_rate", dimension="product") == "refund_quality"


def test_signal_policy_selects_related_signal_for_root_cause() -> None:
    assert (
        select_signal_type(
            metric_id="pay_cvr",
            dimension="device",
            root_cause_type="conversion_drop",
        )
        == "conversion"
    )


def test_signal_policy_fails_fast_for_missing_metric_dimension_rule() -> None:
    with pytest.raises(ValueError, match="SIGNAL_POLICY_MISSING"):
        select_signal_type_for_metric_dimension(metric_id="refund_rate", dimension="warehouse")
