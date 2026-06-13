from __future__ import annotations

import importlib
import inspect
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text

from metric_rca.agent.reflection import verify_reflection
from metric_rca.agent.runner import RunOrchestrator
from metric_rca.config.settings import Settings
from metric_rca.domain.models import Evidence, RootCauseCandidate
from metric_rca.guardrails.query_spec import build_query_spec
from tests.test_orchestrator import _Agent, _FailingMemoryRepo, _Repo, _deps


def test_memory_repo_is_real_not_reexport_shell() -> None:
    module = importlib.import_module("metric_rca.memory.memory_repo")
    repo_cls = getattr(module, "MemoryRepository")
    source = inspect.getsource(module)

    assert repo_cls.__module__ == module.__name__
    assert "class MemoryRepository" in source
    assert "FROM memory_record" in source
    assert "from metric_rca.repositories.metric_repository import MemoryRepository" not in source


def test_memory_repo_from_settings_uses_trusted_sources() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    repo = MemoryRepository.from_settings(
        Settings.model_construct(
            db_dsn="sqlite+pysqlite:///:memory:",
            memory_trusted_sources={"test"},
        )
    )
    try:
        assert repo._trusted_sources == frozenset({"test"})
    finally:
        repo.close()


def test_memory_low_confidence_ignored() -> None:
    repo = _repo()
    repo.write(_record("gmv|channel", {"dimension": "channel"}, version=1, confidence=0.40))

    assert repo.read("gmv|channel") == []


def test_memory_untrusted_source_ignored() -> None:
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
                "memory_id": "untrusted",
                "layer": "case",
                "mem_key": "gmv|category",
                "payload": '{"dimension": "category"}',
                "confidence": 0.95,
                "source": "untrusted",
                "version": 1,
                "ttl_days": 30,
                "created_at": datetime(2026, 6, 1),
            },
        )

    assert repo.read("gmv|category") == []


def test_memory_expired_ignored() -> None:
    repo = _repo(now=datetime(2026, 6, 9))
    repo.write(_record("gmv|channel", {"dimension": "channel"}, version=1, ttl_days=1))

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
            repo.write(_record("gmv|channel", {"dimension": "channel"}, version=1, confidence=confidence))
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


def test_memory_invalid_layer_or_ttl_write_returns_MEMORY_WRITE_FAILED() -> None:
    repo = _repo()

    invalid_records = [
        {"layer": "runtime", "ttl_days": 30},
        {"layer": "case", "ttl_days": 0},
        {"layer": "case", "ttl_days": 30, "source": "untrusted"},
    ]
    for overrides in invalid_records:
        record = _record("gmv|channel", {"dimension": "channel"}, version=1)
        record.update(overrides)
        try:
            repo.write(record)
        except RuntimeError as exc:
            assert str(exc) == "MEMORY_WRITE_FAILED"
        else:
            raise AssertionError("invalid memory layer/ttl was accepted")


def test_memory_required_read_failure_fails_run() -> None:
    repo = _Repo()
    result = RunOrchestrator(
        dependencies=_deps(repo, memory_required=True, memory_repo=_FailingMemoryRepo()),
        agent_factory=lambda **kwargs: _Agent(),
    ).run("why", run_id="run-1")

    assert result["status"] == "failed"
    assert result["error_code"] == "MEMORY_READ_FAILED"


def test_memory_cannot_be_final_conclusion_without_current_evidence() -> None:
    state = {
        "run_id": "run-1",
        "status": "running",
        "metric_id": "gmv",
        "target_date": date(2026, 6, 5),
        "memory_hits": [{"mem_key": "gmv|channel", "root_cause_type": "campaign_traffic_drop"}],
        "candidates": [_candidate(evidence_ids=["memory:gmv|channel"])],
        "evidences": [_evidence("run-1:E1")],
        "repair_count": 1,
    }

    result = verify_reflection(state, max_repair=1, persisted_evidence_by_id={"run-1:E1": _persisted(_evidence("run-1:E1"))})

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
    return MemoryRepository(engine=engine, now_fn=lambda: now, trusted_sources={"test", "reflection_verified"})


def _record(
    mem_key: str,
    payload: dict[str, Any],
    *,
    version: int,
    confidence: float = 0.90,
    ttl_days: int = 30,
) -> dict[str, Any]:
    return {
        "layer": "case",
        "mem_key": mem_key,
        "payload": payload,
        "confidence": confidence,
        "source": "test",
        "version": version,
        "ttl_days": ttl_days,
        "created_at": datetime(2026, 6, 1) + timedelta(seconds=version),
    }


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
        query_spec=build_query_spec(metric_id="gmv", start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)),
        sql="SELECT 1",
        sql_hash="0" * 64,
        guard_status="passed",
        result_summary={"metric_id": "gmv"},
        data_source="fact_order",
        created_at=datetime(2026, 6, 5),
    )


def _persisted(evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "run_id": evidence.evidence_id.split(":", maxsplit=1)[0],
        "query_spec": evidence.query_spec.model_dump(mode="json"),
        "sql_hash": evidence.sql_hash,
        "guard_status": evidence.guard_status,
        "result_summary": evidence.result_summary,
    }
