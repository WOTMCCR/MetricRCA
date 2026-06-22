from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from metric_rca.agent.runner import AgentDependencies
from metric_rca.config.settings import Settings
from metric_rca.guardrails.renderer import SQLRenderer
from metric_rca.observability.trace import TraceWriter
from metric_rca.repositories.metadata_repository import MetadataRepository
from metric_rca.repositories.metric_repository import MetricRepository
from metric_rca.runtime.run_service import RunService
from metric_rca.services.metric_contracts import MetricServiceError, ParsedIntent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_DSN = "mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca"
DEFAULT_READONLY_DB_DSN = "mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca"
TARGET_DATE = date(2026, 6, 5)
NO_ANOMALY_DATE = date(2026, 6, 4)
BUSINESS_TODAY = date(2026, 6, 6)


pytestmark = pytest.mark.skipif(
    os.getenv("METRIC_RCA_E2E_SMOKE") != "1",
    reason="E2E smoke runs only through make test-e2e",
)


def test_e2e_smoke_runs_three_cases_through_run_service() -> None:
    if not _mysql_available():
        pytest.skip("MySQL unavailable for E2E smoke")
    _seed_smoke_profile()

    settings = _settings()
    repository = MetricRepository.from_settings(settings)
    metadata = MetadataRepository.from_settings(settings)
    try:
        metric_service = _SmokeMetricService(metadata, _smoke_intents())
        service = RunService(
            dependencies=AgentDependencies(
                settings=settings,
                repository=repository,
                metric_service=metric_service,
                renderer=SQLRenderer(),
                trace_writer=TraceWriter(repository),
                memory_repo=None,
            )
        )

        for question in metric_service.questions:
            run_id = f"e2e-smoke-{uuid4().hex}"
            result = service.run(question, run_id=run_id)

            assert result["status"] == metric_service.expected_status(question)
            evidences = repository.get_evidences(run_id)
            sql_audit = repository.get_sql_audit_rows(run_id)
            trace_steps = repository.get_trace_steps(run_id)
            assert repository.get_agent_run(run_id) is not None
            assert evidences
            assert sql_audit
            assert trace_steps
            assert all(row["run_id"] == run_id for row in evidences)
            assert all(row["run_id"] == run_id for row in sql_audit)
    finally:
        repository.close()
        metadata._engine.dispose()


class _SmokeMetricService:
    def __init__(self, metadata: MetadataRepository, intents: dict[str, ParsedIntent]) -> None:
        self._metadata = metadata
        self._intents = intents

    @property
    def questions(self) -> list[str]:
        return list(self._intents)

    def parse_question(self, question: str, *, business_today: date) -> ParsedIntent:
        if business_today != BUSINESS_TODAY:
            raise MetricServiceError("DATE_RANGE_INVALID", "unexpected business_today for smoke test")
        intent = self._intents.get(question)
        if intent is None:
            raise MetricServiceError("PARSE_FAILED", f"unknown smoke question: {question}")
        return intent

    def get_metric_definition(self, metric_id: str) -> Any:
        return self._metadata.get_metric_definition(metric_id)

    def expected_status(self, question: str) -> str:
        intent = self._intents[question]
        return "no_anomaly" if intent.target_date == NO_ANOMALY_DATE else "succeeded"


def _smoke_intents() -> dict[str, ParsedIntent]:
    return {
        "Why did paid ads GMV drop?": ParsedIntent(
            metric_id="gmv",
            target_date=TARGET_DATE,
            question_family="gmv_drop",
            dimension="channel",
            element="paid_ads",
        ),
        "Why did electronics GMV drop?": ParsedIntent(
            metric_id="gmv",
            target_date=TARGET_DATE,
            question_family="gmv_drop",
            dimension="category",
            element="electronics",
        ),
        "Was GMV normal on 2026-06-04?": ParsedIntent(
            metric_id="gmv",
            target_date=NO_ANOMALY_DATE,
            question_family="gmv_drop",
        ),
    }


def _settings() -> Settings:
    return Settings(
        db_dsn=_db_dsn(),
        readonly_db_dsn=_readonly_db_dsn(),
        business_today=BUSINESS_TODAY,
        target_date=TARGET_DATE,
        llm_enabled=False,
        llm_required=False,
        memory_enabled=False,
        memory_required=False,
        memory_write_on_finalize=False,
    )


def _seed_smoke_profile() -> None:
    env = os.environ.copy()
    env["PATH"] = f"{PROJECT_ROOT / '.venv' / 'bin'}:{env.get('PATH', '')}"
    env["METRIC_RCA_DB_DSN"] = _db_dsn()
    env["METRIC_RCA_READONLY_DB_DSN"] = _readonly_db_dsn()
    subprocess.run(
        ["make", "seed", "SEED_PROFILE=smoke"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        timeout=90,
    )


def _mysql_available() -> bool:
    engine = create_engine(_db_dsn(), pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _db_dsn() -> str:
    return os.getenv("METRIC_RCA_DB_DSN", DEFAULT_DB_DSN)


def _readonly_db_dsn() -> str:
    return os.getenv("METRIC_RCA_READONLY_DB_DSN", DEFAULT_READONLY_DB_DSN)
