"""Repository for auditable case/session memory over memory_record."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from json import JSONDecodeError
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from metric_rca.config.settings import Settings


ALLOWED_MEMORY_LAYERS = frozenset({"case", "semantic", "episodic", "reflection"})
DEFAULT_TRUSTED_MEMORY_SOURCES = frozenset({"reflection_verified", "system_verified"})
DEFAULT_LAYER_READ_ORDER = ("semantic", "episodic", "reflection", "case")


class MemoryRepository:
    """Reads and writes memory_record with confidence, TTL, and version controls."""

    def __init__(
        self,
        *,
        engine: Engine,
        min_confidence: float = 0.70,
        trusted_sources: set[str] | frozenset[str] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        system_writer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._engine = engine
        self._min_confidence = min_confidence
        self._trusted_sources = (
            frozenset(trusted_sources)
            if trusted_sources is not None
            else DEFAULT_TRUSTED_MEMORY_SOURCES
        )
        self._now_fn = now_fn or _now
        self._system_writer = system_writer

    @classmethod
    def from_settings(cls, settings: Settings, *, system_repository: Any | None = None) -> MemoryRepository:
        system_writer = None
        if system_repository is not None:
            system_writer = getattr(system_repository, "create_memory_record", None)
            if not callable(system_writer):
                raise RuntimeError("MEMORY_WRITE_FAILED: system repository lacks create_memory_record")
        return cls(
            engine=create_engine(str(settings.db_dsn), pool_pre_ping=True),
            trusted_sources=getattr(settings, "memory_trusted_sources", DEFAULT_TRUSTED_MEMORY_SOURCES),
            system_writer=system_writer,
        )

    def read(self, mem_key: str, *, layer: str = "case") -> list[dict[str, Any]]:
        if layer not in ALLOWED_MEMORY_LAYERS:
            raise RuntimeError("MEMORY_READ_FAILED")
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT memory_id, layer, mem_key, payload, confidence, source,
                               version, ttl_days, created_at
                        FROM memory_record
                        WHERE layer = :layer AND mem_key = :mem_key
                        ORDER BY version DESC, created_at DESC
                        """
                    ),
                    {"layer": layer, "mem_key": mem_key},
                ).mappings().all()
        except SQLAlchemyError as exc:
            raise RuntimeError("MEMORY_READ_FAILED") from exc

        try:
            valid = [self._decode_row(row) for row in rows if self._row_is_usable(row)]
        except (JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("MEMORY_READ_FAILED") from exc
        if not valid:
            return []
        return [self._public_hit(row) for row in valid]

    def read_layers(
        self,
        mem_key: str,
        *,
        layers: tuple[str, ...] | list[str] | None = None,
    ) -> list[dict[str, Any]]:
        ordered_layers = tuple(layers) if layers is not None else DEFAULT_LAYER_READ_ORDER
        if any(layer not in ALLOWED_MEMORY_LAYERS for layer in ordered_layers):
            raise RuntimeError("MEMORY_READ_FAILED")
        hits: list[dict[str, Any]] = []
        for layer in ordered_layers:
            hits.extend(self.read(mem_key, layer=layer))
        return hits

    def freeze_legacy_layers(self) -> int:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE memory_record
                        SET layer = 'case'
                        WHERE layer IS NULL
                           OR layer NOT IN ('case', 'semantic', 'episodic', 'reflection')
                        """
                    )
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED") from exc
        return int(result.rowcount or 0)

    def write(self, record: dict[str, Any]) -> None:
        try:
            row = self._normalize_record(record)
        except RuntimeError:
            raise
        except (JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED") from exc
        if self._system_writer is not None:
            try:
                self._system_writer(row)
            except RuntimeError as exc:
                raise RuntimeError("MEMORY_WRITE_FAILED") from exc
            return
        try:
            with self._engine.begin() as conn:
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
                    {**row, "payload": json.dumps(row["payload"])},
                )
        except SQLAlchemyError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED") from exc

    def close(self) -> None:
        self._engine.dispose()

    def _row_is_usable(self, row: Any) -> bool:
        if str(row["source"]) not in self._trusted_sources:
            return False
        if float(row["confidence"]) < self._min_confidence:
            return False
        ttl_days = row["ttl_days"]
        if ttl_days is None:
            return True
        created_at = _as_datetime(row["created_at"])
        return created_at + timedelta(days=int(ttl_days)) >= self._now_fn()

    def _decode_row(self, row: Any) -> dict[str, Any]:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("MEMORY_READ_FAILED")
        return {
            "memory_id": row["memory_id"],
            "layer": row["layer"],
            "mem_key": row["mem_key"],
            "payload": payload,
            "confidence": float(row["confidence"]),
            "source": row["source"],
            "version": int(row["version"]),
            "ttl_days": row["ttl_days"],
            "created_at": _as_datetime(row["created_at"]),
        }

    def _public_hit(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row["payload"])
        return {
            **payload,
            "memory_id": row["memory_id"],
            "layer": row["layer"],
            "mem_key": row["mem_key"],
            "confidence": row["confidence"],
            "source": row["source"],
            "version": row["version"],
            "ttl_days": row["ttl_days"],
            "created_at": row["created_at"],
        }

    def _normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        mem_key = record.get("mem_key") or record.get("key")
        if not mem_key:
            raise RuntimeError("MEMORY_WRITE_FAILED")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("MEMORY_WRITE_FAILED")
        confidence = float(record.get("confidence", 0.80))
        if confidence < 0.0 or confidence > 1.0:
            raise RuntimeError("MEMORY_WRITE_FAILED")
        layer = str(record.get("layer", "case"))
        if layer not in ALLOWED_MEMORY_LAYERS:
            raise RuntimeError("MEMORY_WRITE_FAILED")
        source = record.get("source")
        if not source:
            raise RuntimeError("MEMORY_WRITE_FAILED")
        source = str(source)
        if source not in self._trusted_sources:
            raise RuntimeError("MEMORY_WRITE_FAILED")
        ttl_days = _normalize_ttl_days(record.get("ttl_days"))
        version = int(record["version"]) if "version" in record else self._next_version(layer=layer, mem_key=mem_key)
        return {
            "memory_id": record.get("memory_id") or f"mem-{uuid4().hex}",
            "layer": layer,
            "mem_key": mem_key,
            "payload": payload,
            "confidence": confidence,
            "source": source,
            "version": version,
            "ttl_days": ttl_days,
            "created_at": record.get("created_at") or self._now_fn(),
        }

    def _next_version(self, *, layer: str, mem_key: str) -> int:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT MAX(version) AS max_version
                        FROM memory_record
                        WHERE layer = :layer AND mem_key = :mem_key
                        """
                    ),
                    {"layer": layer, "mem_key": mem_key},
                ).mappings().one()
        except SQLAlchemyError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED") from exc
        max_version = row["max_version"]
        return int(max_version or 0) + 1


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_ttl_days(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise RuntimeError("MEMORY_WRITE_FAILED")
    ttl_days = int(value)
    if ttl_days <= 0:
        raise RuntimeError("MEMORY_WRITE_FAILED")
    return ttl_days


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
