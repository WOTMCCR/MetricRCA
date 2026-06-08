from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from metric_rca.domain.models import QuerySpec, TimeRange
from metric_rca.guardrails.query_spec import QuerySpecError, build_query_spec


def _range() -> TimeRange:
    return TimeRange(start_date=date(2026, 6, 5), end_date=date(2026, 6, 5))


def test_query_spec_contract_rejects_shortcuts() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(metric_id="gmv", time_range=_range(), group_by=["channel", "category", "device"])

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="gmv", time_range=_range(), limit=5001)

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="gmv", time_range=_range(), purpose="freeform")

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="gmv", time_range=_range(), raw_sql="SELECT 1")

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="campaign_roi", time_range=_range())

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="gmv", time_range=_range(), group_by=["warehouse"])

    with pytest.raises(ValidationError):
        QuerySpec(metric_id="complaint_rate", time_range=_range(), group_by=["channel"])


def test_build_query_spec_whitelist_errors_are_typed() -> None:
    with pytest.raises(QuerySpecError) as exc:
        build_query_spec(
            metric_id="gmv",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
            group_by=["region"],
        )

    assert exc.value.code == "DIMENSION_NOT_ALLOWED"

    with pytest.raises(QuerySpecError) as exc:
        build_query_spec(
            metric_id="not_a_metric",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
        )

    assert exc.value.code == "QUERY_SPEC_INVALID"

    with pytest.raises(QuerySpecError) as exc:
        build_query_spec(
            metric_id="campaign_roi",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
        )

    assert exc.value.code == "QUERY_SPEC_INVALID"
