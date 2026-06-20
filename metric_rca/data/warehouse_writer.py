"""Atomic writers for generated scenario warehouse artifacts."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping, Sequence


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(
        path,
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
    )


def write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    _write_text_atomic(
        path,
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows),
    )


def write_csv_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError("WAREHOUSE_CSV_ROWS_EMPTY")
    fields = sorted({str(key) for row in rows for key in row if not str(key).startswith("_")})
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field) for field in fields})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_file_manifest(output_dir: Path, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    rows: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name in excluded:
            continue
        rows.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _file_hash(path),
            }
        )
    return rows


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
