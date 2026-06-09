from __future__ import annotations

import importlib
import inspect
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text

from metric_rca.agent.nodes.read_memory import read_memory
from metric_rca.agent.nodes.write_memory import write_memory
from metric_rca.agent.react import next_action
from metric_rca.agent.reflection import verify_reflection
from metric_rca.config.settings import Settings
from metric_rca.domain.models import Evidence, RootCauseCandidate
from metric_rca.guardrails.query_spec import build_query_spec


def test_memory_repo_is_real_not_reexport_shell() -> None:
    module = importlib.import_module("metric_rca.memory.memory_repo")
    repo_cls = getattr(module, "MemoryRepository")
    source = inspect.getsource(module)

    assert repo_cls.__module__ == module.__name__
    assert "class MemoryRepository" in source
    assert "FROM memory_record" in source
    assert "from metric_rca.repositories.metric_repository import MemoryRepository" not in source


def test_memory_exact_key_hit_reorders_drilldown_only() -> None:
    action = next_action(
        {
            "run_id": "run-1",
            "metric_id": "gmv",
            "target_date": date(2026, 6, 5),
            "parsed_spec": {"filters": {}},
            "memory_hits": [{"mem_key": "gmv|category", "dimension": "category", "root_cause_type": "stockout"}],
            "observations": [_observation("detect_anomaly", ["run-1:E1"])],
            "evidences": [{"evidence_id": "run-1:E1"}],
            "candidates": [],
            "step_count": 1,
            "query_count": 2,
            "drilldown_depth": 0,
        },
        settings=_settings(),
        metric_service=_MetricService(["channel", "category"]),
    )

    assert action.action == "drilldown_dimension"
    assert action.args["dimension"] == "category"
    assert "root_cause_type" not in action.args


def test_memory_low_confidence_ignored() -> None:
    repo = _repo()
    repo.write(
        {
            "layer": "case",
            "mem_key": "gmv|channel",
            "payload": {"dimension": "channel"},
            "confidence": 0.40,
            "source": "test",
            "version": 1,
            "ttl_days": 30,
            "created_at": datetime(2026, 6, 1),
        }
    )

    assert repo.read("gmv|channel") == []


def test_memory_expired_ignored() -> None:
    repo = _repo(now=datetime(2026, 6, 9))
    repo.write(
        {
            "layer": "case",
            "mem_key": "gmv|channel",
            "payload": {"dimension": "channel"},
            "confidence": 0.90,
            "source": "test",
            "version": 1,
            "ttl_days": 1,
            "created_at": datetime(2026, 6, 1),
        }
    )

    assert repo.read("gmv|channel") == []


def test_memory_version_conflict_higher_version_wins() -> None:
    repo = _repo()
    repo.write(_record("gmv|channel", {"dimension": "channel"}, version=1))
    repo.write(_record("gmv|channel", {"dimension": "category"}, version=3))
    repo.write(_record("gmv|channel", {"dimension": "device"}, version=2))

    hits = repo.read("gmv|channel")

    assert len(hits) == 1
    assert hits[0]["version"] == 3
    assert hits[0]["dimension"] == "category"


def test_memory_write_without_version_bumps_existing_highest_version() -> None:
    repo = _repo()
    repo.write(_record("gmv|channel", {"dimension": "channel"}, version=2))
    repo.write(
        {
            "layer": "case",
            "mem_key": "gmv|channel",
            "payload": {"dimension": "device"},
            "confidence": 0.90,
            "source": "test",
            "ttl_days": 30,
            "created_at": datetime(2026, 6, 2),
        }
    )

    hits = repo.read("gmv|channel")

    assert hits[0]["version"] == 3
    assert hits[0]["dimension"] == "device"


def test_memory_invalid_confidence_rejected() -> None:
    repo = _repo()

    for confidence in [-0.1, 1.1]:
        try:
            repo.write(
                {
                    "layer": "case",
                    "mem_key": "gmv|channel",
                    "payload": {"dimension": "channel"},
                    "confidence": confidence,
                    "source": "test",
                    "ttl_days": 30,
                }
            )
        except RuntimeError as exc:
            assert str(exc) == "MEMORY_WRITE_FAILED"
        else:
            raise AssertionError("invalid confidence was accepted")


def test_memory_corrupted_payload_read_returns_typed_failure() -> None:
    for memory_id, payload in [("bad-json", "{bad"), ("json-list", '["x"]')]:
        repo = _repo()
        with repo._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO memory_record (
                      memory_id, layer, mem_key, payload, confidence, source,
                      version, ttl_days, created_at
                    )
                    VALUES (
                      :memory_id, :layer, :mem_key, :payload, :confidence, :source,
                      :version, :ttl_days, :created_at
                    )
                    """
                ),
                {
                    "memory_id": memory_id,
                    "layer": "case",
                    "mem_key": "gmv|channel",
                    "payload": payload,
                    "confidence": 0.90,
                    "source": "test",
                    "version": 1,
                    "ttl_days": 30,
                    "created_at": datetime(2026, 6, 1),
                },
            )

        try:
            repo.read("gmv|channel")
        except RuntimeError as exc:
            assert str(exc) == "MEMORY_READ_FAILED"
        else:
            raise AssertionError("corrupted memory payload was accepted")


def test_memory_invalid_version_write_returns_typed_failure() -> None:
    repo = _repo()

    try:
        repo.write(
            {
                "layer": "case",
                "mem_key": "gmv|channel",
                "payload": {"dimension": "channel"},
                "confidence": 0.90,
                "source": "test",
                "version": "not-an-int",
                "ttl_days": 30,
            }
        )
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_WRITE_FAILED"
    else:
        raise AssertionError("invalid memory version was accepted")


def test_memory_required_read_failure_fails_run() -> None:
    result = read_memory(
        {"run_id": "run-1", "metric_id": "gmv", "parsed_spec": {"dimension": "channel"}},
        dependencies=_Dependencies(settings=_settings(memory_enabled=True, memory_required=True), memory_repo=_FailingMemoryRepo()),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_READ_FAILED"


def test_memory_required_write_failure_fails_run() -> None:
    result = write_memory(
        {
            "run_id": "run-1",
            "status": "succeeded",
            "metric_id": "gmv",
            "parsed_spec": {"dimension": "channel"},
            "report": {"status": "succeeded"},
            "candidates": [_candidate()],
        },
        dependencies=_Dependencies(settings=_settings(memory_enabled=True, memory_required=True), memory_repo=_FailingMemoryRepo()),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_WRITE_FAILED"


def test_memory_enabled_false_does_not_call_repo() -> None:
    repo = _CountingMemoryRepo()
    deps = _Dependencies(settings=_settings(memory_enabled=False), memory_repo=repo)

    assert read_memory({"run_id": "run-1", "metric_id": "gmv"}, dependencies=deps) == {"memory_hits": []}
    assert write_memory({"run_id": "run-1", "status": "succeeded"}, dependencies=deps) == {}
    assert repo.read_calls == 0
    assert repo.write_calls == 0


def test_no_anomaly_write_memory_does_not_persist_conclusion_status() -> None:
    repo = _CountingMemoryRepo()
    deps = _Dependencies(settings=_settings(memory_enabled=True, memory_required=True), memory_repo=repo)

    result = write_memory(
        {
            "run_id": "run-1",
            "status": "no_anomaly",
            "metric_id": "gmv",
            "parsed_spec": {"dimension": "channel"},
            "report": {"status": "no_anomaly"},
            "candidates": [],
        },
        dependencies=deps,
    )

    assert result == {}
    assert repo.write_calls == 0


def test_memory_cannot_be_final_conclusion_without_current_evidence() -> None:
    state = {
        "run_id": "run-1",
        "status": "running",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "memory_hits": [{"mem_key": "gmv|channel", "root_cause_type": "campaign_traffic_drop"}],
        "candidates": [
            _candidate(evidence_ids=["memory:gmv|channel"]),
        ],
        "evidences": [_evidence("run-1:E1")],
        "repair_count": 1,
    }

    result = verify_reflection(state, max_repair=1)

    assert result.passed is False
    assert "current_run_evidence" in {issue.check for issue in result.issues}


def _repo(*, now: datetime = datetime(2026, 6, 5)):
    from metric_rca.memory.memory_repo import MemoryRepository

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE memory_record (
                  memory_id VARCHAR(64) PRIMARY KEY,
                  layer VARCHAR(16) NOT NULL,
                  mem_key VARCHAR(128) NOT NULL,
                  payload TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  source VARCHAR(64) NOT NULL,
                  version INTEGER NOT NULL,
                  ttl_days INTEGER,
                  created_at DATETIME NOT NULL
                )
                """
            )
        )
    return MemoryRepository(engine=engine, now_fn=lambda: now)


def _record(mem_key: str, payload: dict[str, Any], *, version: int) -> dict[str, Any]:
    return {
        "layer": "case",
        "mem_key": mem_key,
        "payload": payload,
        "confidence": 0.90,
        "source": "test",
        "version": version,
        "ttl_days": 30,
        "created_at": datetime(2026, 6, 1) + timedelta(seconds=version),
    }


def _settings(**overrides: Any):
    defaults = {
        "memory_enabled": False,
        "memory_required": False,
        "llm_enabled": False,
        "llm_required": False,
        "max_steps": 8,
        "max_query": 12,
        "max_drilldown_depth": 2,
        "max_repair": 1,
    }
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


def _observation(action_name: str, evidence_ids: list[str]):
    from metric_rca.domain.models import Observation

    return Observation(action_name=action_name, ok=True, evidence_ids=evidence_ids).model_dump()


def _candidate(*, evidence_ids: list[str] | None = None) -> RootCauseCandidate:
    return RootCauseCandidate(
        root_cause_type="campaign_traffic_drop",
        dimension="channel",
        element="paid_ads",
        contribution_pct=0.90,
        signal_severity=0.90,
        evidence_support=1.0,
        eng_confidence=0.90,
        verdict="confirmed",
        evidence_ids=evidence_ids if evidence_ids is not None else ["run-1:E1"],
    )


def _evidence(evidence_id: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        query_spec=build_query_spec(
            metric_id="gmv",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
        ),
        sql="SELECT 1",
        sql_hash="0" * 64,
        guard_status="passed",
        result_summary={"metric_id": "gmv"},
        data_source="fact_order",
        created_at=datetime(2026, 6, 5),
    )


class _MetricService:
    def __init__(self, dimensions: list[str]) -> None:
        self.dimensions = dimensions

    def get_metric_definition(self, metric_id: str):
        return type("Definition", (), {"allowed_dimensions": self.dimensions})()


class _Dependencies:
    def __init__(self, *, settings=None, memory_repo=None) -> None:
        self.settings = settings or _settings()
        self.metric_service = _MetricService(["channel", "category"])
        self.memory_repo = memory_repo
        self.trace_writer = _TraceWriter()


class _TraceWriter:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    def write_step(self, **kwargs: Any) -> None:
        self.steps.append(kwargs)

    def finish_run(self, **kwargs: Any) -> None:
        self.finished.append(kwargs)


class _FailingMemoryRepo:
    def read(self, *args: Any, **kwargs: Any):
        raise RuntimeError("MEMORY_READ_FAILED")

    def write(self, *args: Any, **kwargs: Any):
        raise RuntimeError("MEMORY_WRITE_FAILED")


class _CountingMemoryRepo:
    def __init__(self) -> None:
        self.read_calls = 0
        self.write_calls = 0

    def read(self, *args: Any, **kwargs: Any):
        self.read_calls += 1
        return []

    def write(self, *args: Any, **kwargs: Any):
        self.write_calls += 1
