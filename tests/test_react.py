from __future__ import annotations

from datetime import date

from metric_rca.agent.react import ALLOWED_ACTIONS, next_action, validate_action
from metric_rca.config.settings import Settings
from metric_rca.domain.models import AgentAction, Observation


def _settings(**overrides):
    defaults = {
        "db_dsn": "mysql+pymysql://writer:writer@127.0.0.1:3307/metric_rca",
        "readonly_db_dsn": "mysql+pymysql://reader:reader@127.0.0.1:3307/metric_rca",
        "llm_enabled": False,
        "llm_provider": None,
        "llm_api_key": None,
        "memory_enabled": False,
    }
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


def test_allowed_actions_exact_contract() -> None:
    assert ALLOWED_ACTIONS == [
        "detect_anomaly",
        "drilldown_dimension",
        "fetch_related_signal",
        "calculate_contribution",
        "finish",
    ]


def test_illegal_action_records_ACTION_SCHEMA_INVALID_and_does_not_execute_tool() -> None:
    validated, observation = validate_action(AgentAction(action="drop_table", args={"x": 1}))

    assert validated is None
    assert observation == Observation(
        action_name="drop_table",
        ok=False,
        error_code="ACTION_SCHEMA_INVALID",
        message="action is not allowed",
    )


def test_bad_args_records_ACTION_SCHEMA_INVALID() -> None:
    validated, observation = validate_action(
        AgentAction(action="drilldown_dimension", args={"dimension": "channel"})
    )

    assert validated is None
    assert observation is not None
    assert observation.ok is False
    assert observation.error_code == "ACTION_SCHEMA_INVALID"


def test_policy_yields_documented_gmv_sequence_from_state() -> None:
    base_state = {
        "run_id": "run-1",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "parsed_spec": {
            "metric_id": "gmv",
            "target_date": "2026-06-05",
            "question_family": "gmv_drop",
            "dimension": None,
            "element": None,
            "filters": {},
        },
        "memory_hits": [],
        "actions": [],
        "observations": [],
        "evidences": [],
        "candidates": [],
        "step_count": 0,
        "query_count": 0,
        "drilldown_depth": 0,
        "repair_count": 0,
    }

    first = next_action(base_state, settings=_settings(), metric_service=_MetricService(["channel"]))
    assert first.action == "detect_anomaly"

    second = next_action(
        {
            **base_state,
            "observations": [
                Observation(action_name="detect_anomaly", ok=True, evidence_ids=["run-1:E1"]).model_dump()
            ],
            "evidences": [{"evidence_id": "run-1:E1"}],
        },
        settings=_settings(),
        metric_service=_MetricService(["channel"]),
    )
    assert second.action == "drilldown_dimension"
    assert second.args["dimension"] == "channel"

    third = next_action(
        {
            **base_state,
            "observations": [
                Observation(action_name="detect_anomaly", ok=True, evidence_ids=["run-1:E1"]).model_dump(),
                Observation(
                    action_name="drilldown_dimension",
                    ok=True,
                    evidence_ids=["run-1:E2"],
                    payload={
                        "candidates": [
                            {
                                "dimension": "channel",
                                "element": "paid_ads",
                                "contribution_pct": 0.9,
                            }
                        ]
                    },
                ).model_dump(),
            ],
            "evidences": [{"evidence_id": "run-1:E1"}, {"evidence_id": "run-1:E2"}],
        },
        settings=_settings(signal_metric_by_type={"campaign": "gmv"}),
        metric_service=_MetricService(["channel"]),
    )
    assert third.action == "fetch_related_signal"
    assert third.args["signal_type"] == "campaign"
    assert third.args["element"] == "paid_ads"


def test_llm_required_unavailable_fails() -> None:
    action = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "parsed_spec": {"filters": {}},
            "actions": [],
            "observations": [],
            "evidences": [],
            "step_count": 0,
        },
        settings=_settings(llm_enabled=True, llm_required=True, llm_provider=None, llm_api_key=None),
        metric_service=_MetricService(["channel"]),
    )

    assert action.action == "finish"
    assert action.args["error_code"] == "LLM_REQUIRED_UNAVAILABLE"


def test_query_and_drilldown_limits_fail_by_business_error() -> None:
    query_limit = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "parsed_spec": {"filters": {}},
            "actions": [],
            "observations": [],
            "evidences": [],
            "step_count": 0,
            "query_count": 12,
            "drilldown_depth": 0,
        },
        settings=_settings(max_query=12),
        metric_service=_MetricService(["channel"]),
    )
    drilldown_limit = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "parsed_spec": {"filters": {}},
            "actions": [],
            "observations": [],
            "evidences": [],
            "step_count": 0,
            "query_count": 0,
            "drilldown_depth": 2,
        },
        settings=_settings(max_drilldown_depth=2),
        metric_service=_MetricService(["channel"]),
    )

    assert query_limit.action == "finish"
    assert query_limit.args["error_code"] == "MAX_QUERY_EXCEEDED"
    assert drilldown_limit.action == "finish"
    assert drilldown_limit.args["error_code"] == "MAX_DRILLDOWN_DEPTH_EXCEEDED"


class _MetricService:
    def __init__(self, dimensions: list[str]) -> None:
        self.dimensions = dimensions

    def get_metric_definition(self, metric_id: str):
        return type("Definition", (), {"metric_id": metric_id, "allowed_dimensions": self.dimensions})()
