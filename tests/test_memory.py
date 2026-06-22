from __future__ import annotations

import importlib
import inspect
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text

from metric_rca.agent.reflection import verify_reflection
from metric_rca.config.settings import Settings
from metric_rca.domain.models import Evidence, MetricDefinition, RootCauseCandidate
from metric_rca.guardrails.query_spec import build_query_spec
from metric_rca.runtime.plan_compiler import RcaPlanCompiler
from metric_rca.runtime.plan_models import CasePrior
from metric_rca.runtime.memory_service import RuntimeMemoryService
from metric_rca.services.metric_contracts import ParsedIntent


class _MetricCatalog:
    def get_metric_definition(self, metric_id: str) -> MetricDefinition:
        return MetricDefinition(
            metric_id=metric_id,
            display_name=metric_id,
            formula="test",
            metric_family="gmv_family",
            source_table="fact_order",
            allowed_dimensions=["channel", "category", "device", "product"],
        )


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


def test_memory_repo_from_settings_rejects_incompatible_system_repository() -> None:
    from metric_rca.memory.memory_repo import MemoryRepository

    try:
        MemoryRepository.from_settings(
            Settings.model_construct(db_dsn="sqlite+pysqlite:///:memory:"),
            system_repository=object(),
        )
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_WRITE_FAILED: system repository lacks create_memory_record"
    else:
        raise AssertionError("incompatible system_repository must fail fast")


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

    assert hits[0]["version"] == 3
    assert hits[0]["dimension"] == "category"
    assert [hit["version"] for hit in hits] == [3, 2, 1]


def test_memory_read_layers_returns_older_scoped_hits_for_runner_filtering() -> None:
    repo = _repo()
    older = _record(
        "gmv|run",
        {"metric_id": "gmv", "filters": {"category": "electronics"}, "run_id": "electronics-run"},
        version=1,
    )
    older["layer"] = "episodic"
    newer = _record(
        "gmv|run",
        {"metric_id": "gmv", "filters": {"category": "fashion"}, "run_id": "fashion-run"},
        version=2,
    )
    newer["layer"] = "episodic"
    repo.write(older)
    repo.write(newer)

    hits = repo.read_layers("gmv|run", layers=("episodic",))

    assert [hit["run_id"] for hit in hits] == ["fashion-run", "electronics-run"]


def test_memory_repo_reads_all_four_layers_for_run_context() -> None:
    repo = _repo()
    for index, layer in enumerate(["semantic", "episodic", "reflection", "case"], start=1):
        record = _record("gmv|run", {"metric_id": "gmv", "layer_payload": layer}, version=index)
        record["layer"] = layer
        repo.write(record)

    hits = repo.read_layers("gmv|run")

    assert [hit["layer"] for hit in hits] == ["semantic", "episodic", "reflection", "case"]
    assert {hit["layer_payload"] for hit in hits} == {"semantic", "episodic", "reflection", "case"}


def test_memory_repo_freezes_unknown_legacy_layers_to_case() -> None:
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
                "memory_id": "legacy-layer",
                "layer": "runtime",
                "mem_key": "gmv|legacy",
                "payload": '{"metric_id": "gmv"}',
                "confidence": 0.90,
                "source": "test",
                "version": 1,
                "ttl_days": 30,
                "created_at": datetime(2026, 6, 1),
            },
        )

    repo.freeze_legacy_layers()

    with repo._engine.connect() as conn:
        layer = conn.execute(
            text("SELECT layer FROM memory_record WHERE memory_id = 'legacy-layer'")
        ).scalar_one()
    assert layer == "case"


def test_memory_repo_freezes_null_legacy_layers_to_case() -> None:
    repo = _repo(layer_nullable=True)
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
                "memory_id": "legacy-null-layer",
                "layer": None,
                "mem_key": "gmv|legacy",
                "payload": '{"metric_id": "gmv"}',
                "confidence": 0.90,
                "source": "test",
                "version": 1,
                "ttl_days": 30,
                "created_at": datetime(2026, 6, 1),
            },
        )

    repo.freeze_legacy_layers()

    with repo._engine.connect() as conn:
        layer = conn.execute(
            text("SELECT layer FROM memory_record WHERE memory_id = 'legacy-null-layer'")
        ).scalar_one()
    assert layer == "case"


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


def test_memory_write_uses_injected_system_table_writer() -> None:
    writes: list[dict[str, Any]] = []
    repo = _repo(system_writer=writes.append)

    repo.write(_record("gmv|channel", {"dimension": "channel"}, version=1))

    assert len(writes) == 1
    assert writes[0]["memory_id"].startswith("mem-")
    assert writes[0]["layer"] == "case"
    assert writes[0]["mem_key"] == "gmv|channel"
    assert writes[0]["payload"] == {"dimension": "channel"}
    assert writes[0]["confidence"] == 0.90


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


def test_memory_read_rejects_invalid_layer() -> None:
    repo = _repo()

    try:
        repo.read("gmv|run", layer="runtime")
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_READ_FAILED"
    else:
        raise AssertionError("invalid memory read layer was accepted")


def test_case_prior_is_planning_hint_not_evidence() -> None:
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["channel"],
        preferred_signal_types=["campaign"],
        prior_root_causes=["campaign_traffic_drop"],
        confidence=0.8,
        source_memory_ids=["mem-1"],
    )

    assert prior.preferred_dimensions == ["channel"]
    assert prior.source_memory_ids == ["mem-1"]


def test_runtime_memory_rejects_answer_bearing_prior_without_empty_fallback() -> None:
    trace = _RuntimeTraceWriter()
    service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(
                hits_by_key={
                    "gmv|run": [
                        {
                            "memory_id": "mem-poison",
                            "preferred_dimensions": ["channel"],
                            "root_cause_type": "campaign_traffic_drop",
                            "nested": {"expected_element": "paid_ads"},
                            "confidence": 0.95,
                            "source": "reflection_verified",
                        }
                    ]
                }
            ),
            trace_writer=trace,
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False),
        )
    )

    try:
        service.read_priors(
            "run-1",
            ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop"),
        )
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_READ_FAILED: memory read failed"
    else:
        raise AssertionError("answer-bearing memory must fail, not degrade to empty priors")
    assert trace.steps[-1]["error_code"] == "MEMORY_READ_FAILED"


def test_runtime_memory_ignores_answer_bearing_prior_scoped_to_other_eval_suite() -> None:
    trace = _RuntimeTraceWriter()
    service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(
                hits_by_key={
                    "gmv|run": [
                        {
                            "memory_id": "mem-other-suite-poison",
                            "eval_suites": ["memory-treatment"],
                            "preferred_dimensions": ["product"],
                            "nested": {"expected_element": "2"},
                            "question_family": "gmv_drop",
                            "analysis_strategy": "standard",
                            "confidence": 0.95,
                            "source": "system_verified",
                        }
                    ]
                }
            ),
            trace_writer=trace,
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False, eval_suite="regression"),
        )
    )

    priors = service.read_priors(
        "run-1",
        ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop"),
    )

    assert priors == []
    assert trace.steps[-1]["error_code"] is None
    assert trace.steps[-1]["output_summary"]["excluded_hit_count"] == 1


def test_runtime_memory_write_failure_is_typed_even_when_not_required() -> None:
    service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(write_error=RuntimeError("MEMORY_WRITE_FAILED")),
            trace_writer=_RuntimeTraceWriter(),
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False, memory_write_on_finalize=True),
        )
    )

    try:
        service.write_verified_case(
            "run-1",
            {"status": "succeeded", "top_candidate": {"dimension": "channel", "root_cause_type": "campaign_traffic_drop"}},
            _RuntimeReflection(),
            ParsedIntent(metric_id="gmv", target_date=date(2026, 6, 5), question_family="gmv_drop"),
        )
    except RuntimeError as exc:
        assert str(exc) == "MEMORY_WRITE_FAILED: memory write failed"
    else:
        raise AssertionError("memory write failure must fail fast")


def test_runtime_memory_filters_case_priors_by_intent_contract() -> None:
    trace = _RuntimeTraceWriter()
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )
    service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(
                hits_by_key={
                    "gmv|run": [
                        {
                            "memory_id": "mem-old-unscoped",
                            "preferred_dimensions": ["product"],
                            "preferred_signal_types": ["inventory"],
                            "prior_root_causes": ["stockout"],
                            "confidence": 0.95,
                            "source": "reflection_verified",
                            "layer": "episodic",
                            "mem_key": "gmv|run",
                        },
                        {
                            "memory_id": "mem-matching-intent",
                            "preferred_dimensions": ["channel"],
                            "preferred_signal_types": ["campaign"],
                            "prior_root_causes": ["campaign_traffic_drop"],
                            "question_family": "gmv_drop",
                            "analysis_strategy": "standard",
                            "confidence": 0.95,
                            "source": "reflection_verified",
                            "layer": "episodic",
                            "mem_key": "gmv|run",
                        },
                    ]
                }
            ),
            trace_writer=trace,
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False),
        )
    )

    priors = service.read_priors("run-1", parsed)

    assert [prior.source_memory_ids for prior in priors] == [["mem-matching-intent"]]
    assert trace.steps[-1]["output_summary"]["excluded_hit_count"] == 1


def test_runtime_memory_uses_eval_suite_scoped_prior_only_for_matching_suite() -> None:
    hit = {
        "memory_id": "mem-treatment",
        "layer": "case",
        "mem_key": "gmv|run",
        "metric_id": "gmv",
        "question_family": "gmv_drop",
        "analysis_strategy": "standard",
        "eval_suites": ["memory-treatment"],
        "preferred_dimensions": ["product"],
        "preferred_signal_types": ["inventory"],
        "prior_root_causes": ["aov_drop"],
        "confidence": 0.95,
        "source": "system_verified",
    }
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="standard",
    )

    regression_service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(hits_by_key={"gmv|run": [hit]}),
            trace_writer=_RuntimeTraceWriter(),
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False, eval_suite="regression"),
        )
    )
    treatment_trace = _RuntimeTraceWriter()
    treatment_service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=_RuntimeMemoryRepo(hits_by_key={"gmv|run": [hit]}),
            trace_writer=treatment_trace,
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False, eval_suite="memory-treatment"),
        )
    )

    assert regression_service.read_priors("run-regression", parsed) == []
    priors = treatment_service.read_priors("run-treatment", parsed)

    assert len(priors) == 1
    assert priors[0].preferred_dimensions == ["product"]
    assert priors[0].prior_root_causes == ["aov_drop"]
    assert treatment_trace.steps[-1]["input_summary"]["eval_suite"] == "memory-treatment"


def test_runtime_memory_writes_intent_contract_for_future_prior_filtering() -> None:
    repo = _RuntimeMemoryRepo()
    service = RuntimeMemoryService(
        dependencies=_RuntimeMemoryDependencies(
            memory_repo=repo,
            trace_writer=_RuntimeTraceWriter(),
            settings=_RuntimeSettings(memory_enabled=True, memory_required=False, memory_write_on_finalize=True),
        )
    )
    parsed = ParsedIntent(
        metric_id="gmv",
        target_date=date(2026, 6, 5),
        question_family="gmv_drop",
        analysis_strategy="product_first",
    )

    service.write_verified_case(
        "run-1",
        {"status": "succeeded", "top_candidate": {"dimension": "product", "root_cause_type": "aov_drop"}},
        _RuntimeReflection(),
        parsed,
    )

    payload = repo.writes[0]["payload"]
    assert payload["question_family"] == "gmv_drop"
    assert payload["analysis_strategy"] == "product_first"


def test_plan_compiler_keeps_memory_hints_without_skipping_evidence_chain() -> None:
    prior = CasePrior(
        metric_id="gmv",
        preferred_dimensions=["channel"],
        preferred_signal_types=["campaign"],
        prior_root_causes=["campaign_traffic_drop"],
        confidence=0.8,
        source_memory_ids=["mem-1"],
    )

    plan = RcaPlanCompiler(metric_service=_MetricCatalog()).compile(
        run_id="run-1",
        parsed_intent=ParsedIntent(
            metric_id="gmv",
            target_date=date(2026, 6, 5),
            question_family="gmv_drop",
        ),
        memory_hints=[prior],
    )

    assert plan.memory_hints == [prior]
    assert plan.actions[0].kind == "detect_anomaly"
    assert [action.kind for action in plan.actions][-1] == "rank_root_causes"
    assert all("memory" not in evidence_id for action in plan.actions for evidence_id in action.requires)


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


def _repo(
    *,
    now: datetime = datetime(2026, 6, 5),
    system_writer: Any | None = None,
    layer_nullable: bool = False,
):
    from metric_rca.memory.memory_repo import MemoryRepository

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE memory_record (
                  memory_id VARCHAR(64) PRIMARY KEY,
                  layer VARCHAR(16) {layer_constraint},
                  mem_key VARCHAR(128) NOT NULL,
                  payload TEXT NOT NULL,
                  confidence REAL NOT NULL,
                  source VARCHAR(64) NOT NULL,
                  version INTEGER NOT NULL,
                  ttl_days INTEGER,
                  created_at DATETIME NOT NULL
                )
                """.format(layer_constraint="" if layer_nullable else "NOT NULL")
            )
        )
    return MemoryRepository(
        engine=engine,
        now_fn=lambda: now,
        trusted_sources={"test", "reflection_verified"},
        system_writer=system_writer,
    )


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


class _RuntimeSettings:
    def __init__(
        self,
        *,
        memory_enabled: bool,
        memory_required: bool,
        memory_write_on_finalize: bool = True,
        eval_suite: str = "regression",
    ) -> None:
        self.memory_enabled = memory_enabled
        self.memory_required = memory_required
        self.memory_write_on_finalize = memory_write_on_finalize
        self.eval_suite = eval_suite


class _RuntimeMemoryDependencies:
    def __init__(self, *, memory_repo: Any, trace_writer: Any, settings: Any) -> None:
        self.memory_repo = memory_repo
        self.trace_writer = trace_writer
        self.settings = settings


class _RuntimeMemoryRepo:
    def __init__(
        self,
        *,
        hits_by_key: dict[str, list[dict[str, Any]]] | None = None,
        write_error: RuntimeError | None = None,
    ) -> None:
        self._hits_by_key = hits_by_key or {}
        self._write_error = write_error
        self.writes: list[dict[str, Any]] = []

    def read_layers(self, mem_key: str, *, layers: tuple[str, ...]) -> list[dict[str, Any]]:
        return list(self._hits_by_key.get(mem_key, []))

    def write(self, record: dict[str, Any]) -> None:
        if self._write_error is not None:
            raise self._write_error
        self.writes.append(record)


class _RuntimeTraceWriter:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def write_step(self, **kwargs: Any) -> None:
        self.steps.append(kwargs)


class _RuntimeReflection:
    repair_count = 0
