from __future__ import annotations

from pathlib import Path
import re

from metric_rca.data.schema_contract import (
    AGENT_RUN_ID_MAX_LENGTH,
    EVIDENCE_ALIAS_MAX_LENGTH,
    EVIDENCE_REFERENCE_MAX_LENGTH,
)


ROOT = Path(__file__).resolve().parents[1]


def test_schema_matches_evidence_identity_limits() -> None:
    schema = (ROOT / "metric_rca/data/schema.sql").read_text(encoding="utf-8")
    evidence_block = _table_block(schema, "evidence")
    agent_run_block = _table_block(schema, "agent_run")

    assert _varchar_length(agent_run_block, "run_id") == AGENT_RUN_ID_MAX_LENGTH
    assert _varchar_length(evidence_block, "run_id") == AGENT_RUN_ID_MAX_LENGTH
    assert _varchar_length(evidence_block, "alias") == EVIDENCE_ALIAS_MAX_LENGTH
    assert _varchar_length(evidence_block, "evidence_id") == EVIDENCE_REFERENCE_MAX_LENGTH
    assert "UNIQUE KEY uq_evidence_run_alias (run_id, alias)" in evidence_block
    assert "evidence_pk   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY" in evidence_block


def test_existing_database_migration_matches_schema_contract() -> None:
    migration = (
        ROOT
        / "metric_rca/data/migrations/20260620_01_decouple_evidence_identity.sql"
    ).read_text(encoding="utf-8")

    assert f"evidence_id VARCHAR({EVIDENCE_REFERENCE_MAX_LENGTH})" in migration
    assert f"alias VARCHAR({EVIDENCE_ALIAS_MAX_LENGTH})" in migration
    assert "ADD UNIQUE KEY uq_evidence_run_alias (run_id, alias)" in migration


def _table_block(schema: str, table: str) -> str:
    match = re.search(
        rf"CREATE TABLE {re.escape(table)} \((.*?)\) ENGINE=InnoDB;",
        schema,
        re.DOTALL,
    )
    assert match is not None, f"table not found: {table}"
    return match.group(1)


def _varchar_length(block: str, column: str) -> int:
    match = re.search(rf"\b{re.escape(column)}\s+VARCHAR\((\d+)\)", block)
    assert match is not None, f"VARCHAR column not found: {column}"
    return int(match.group(1))
