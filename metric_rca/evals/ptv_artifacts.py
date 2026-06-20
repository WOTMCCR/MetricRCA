"""Canonical PTV artifact layout, atomic persistence, and integrity validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from metric_rca.evals.ptv_errors import PtvErrorCode, PtvRuntimeError


_CYCLE_ID_RE = re.compile(r"^cycle-\d{8}-\d{4}(?:-[a-z0-9][a-z0-9-]{0,31})?$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
ROUND_FILE_NAMES = (
    "round_meta.json",
    "predictions.jsonl",
    "eval-result.json",
    "gap_report.json",
    "diagnosis.jsonl",
    "optimization_summary.json",
    "summary.json",
    "anti_cheat_report.json",
    "artifact_manifest.json",
    "commit_lineage.json",
)
ROUND_EXECUTION_ARTIFACTS = (
    "predictions.jsonl",
    "barrier.json",
    "prediction.log",
    "eval.log",
    "analyst.log",
    "eval-result.json",
    "gap_report.json",
    "analyst_input.json",
    "diagnosis.jsonl",
    "optimization_summary.json",
    "summary.json",
    "anti_cheat_report.json",
    "artifact_manifest.json",
    "per-case",
    "grpo_dataset",
    "runner_grpo_dataset",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_cycle_id(now: datetime | None = None) -> str:
    instant = now or datetime.now(ZoneInfo("Asia/Tokyo"))
    return f"cycle-{instant.astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y%m%d-%H%M')}"


def validate_cycle_id(cycle_id: str) -> str:
    if not _CYCLE_ID_RE.fullmatch(cycle_id):
        raise PtvRuntimeError(
            PtvErrorCode.CYCLE_ID_INVALID,
            "cycle_id must match cycle-YYYYMMDD-HHMM[-suffix]",
            context={"cycle_id": cycle_id},
        )
    return cycle_id


def validate_round_number(round_number: int) -> int:
    if round_number < 1 or round_number > 999:
        raise PtvRuntimeError(
            PtvErrorCode.ROUND_NUMBER_INVALID,
            "round number must be in [1, 999]",
            context={"round": round_number},
        )
    return round_number


def validate_commit(commit: str | None, *, field: str, required: bool) -> str | None:
    if commit is None or not commit.strip():
        if required:
            raise PtvRuntimeError(
                PtvErrorCode.COMMIT_INVALID,
                f"{field} is required",
                context={"field": field},
            )
        return None
    normalized = commit.strip().lower()
    if not _GIT_COMMIT_RE.fullmatch(normalized):
        raise PtvRuntimeError(
            PtvErrorCode.COMMIT_INVALID,
            f"{field} must be a 7-40 character lowercase hexadecimal git commit",
            context={"field": field, "value": commit},
        )
    return normalized


@dataclass(frozen=True)
class PtvLayout:
    output_root: Path = Path("eval_out/ptv")

    def cycle_dir(self, cycle_id: str) -> Path:
        return self.output_root / validate_cycle_id(cycle_id)

    def round_dir(self, cycle_id: str, round_number: int) -> Path:
        return self.cycle_dir(cycle_id) / f"round-{validate_round_number(round_number):02d}"

    def previous_round_dir(self, cycle_id: str, round_number: int) -> Path | None:
        validate_round_number(round_number)
        if round_number == 1:
            return None
        return self.round_dir(cycle_id, round_number - 1)


def create_cycle(
    *,
    layout: PtvLayout,
    cycle_id: str,
    branch: str,
    base_commit: str,
    total_cases: int,
    max_rounds: int,
    project_binding: str = "metricrca",
) -> Path:
    validate_cycle_id(cycle_id)
    validated_commit = validate_commit(base_commit, field="base_commit", required=True)
    if not branch.strip():
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "branch must not be empty")
    if total_cases < 1:
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "total_cases must be positive")
    if max_rounds < 1:
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "max_rounds must be positive")
    cycle_dir = layout.cycle_dir(cycle_id)
    cycle_dir.mkdir(parents=True, exist_ok=True)
    meta_path = cycle_dir / "meta.json"
    payload = {
        "schema_version": "metricrca-ptv-cycle-v2",
        "cycle_id": cycle_id,
        "branch": branch.strip(),
        "base_commit": validated_commit,
        "started_at": utc_now_iso(),
        "total_cases": total_cases,
        "max_rounds": max_rounds,
        "project_binding": project_binding,
        "status": "in_progress",
    }
    if meta_path.exists():
        existing = read_json(meta_path)
        stable_fields = ("cycle_id", "branch", "base_commit", "total_cases", "max_rounds", "project_binding")
        if any(existing.get(field) != payload.get(field) for field in stable_fields):
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_INVALID,
                "existing cycle metadata conflicts with requested cycle",
                context={"path": str(meta_path)},
            )
        return cycle_dir
    write_json_atomic(meta_path, payload)
    return cycle_dir


def create_round(
    *,
    layout: PtvLayout,
    cycle_id: str,
    round_number: int,
    eval_id: str,
    eval_code_commit: str,
    fix_commit: str | None,
    post_eval_review_fix_commit: str | None,
    confirmation_of_round: int | None = None,
) -> Path:
    round_dir = layout.round_dir(cycle_id, round_number)
    round_dir.mkdir(parents=True, exist_ok=True)
    if not eval_id.strip():
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "eval_id must not be empty")
    if confirmation_of_round is not None:
        validate_round_number(confirmation_of_round)
        if confirmation_of_round >= round_number:
            raise PtvRuntimeError(
                PtvErrorCode.TWO_GREEN_INVALID,
                "confirmation_of_round must refer to an earlier round",
            )
    lineage = build_commit_lineage(
        eval_code_commit=eval_code_commit,
        fix_commit=fix_commit,
        post_eval_review_fix_commit=post_eval_review_fix_commit,
    )
    round_meta = {
        "schema_version": "metricrca-ptv-round-v2",
        "cycle_id": cycle_id,
        "round": round_number,
        "eval_id": eval_id.strip(),
        "created_at": utc_now_iso(),
        "status": "prepared",
        "confirmation_of_round": confirmation_of_round,
    }
    meta_path = round_dir / "round_meta.json"
    execution_artifacts = _existing_execution_artifacts(round_dir, eval_id.strip())
    if meta_path.exists():
        existing = read_json(meta_path)
        stable_fields = ("cycle_id", "round", "eval_id", "confirmation_of_round")
        if any(existing.get(field) != round_meta.get(field) for field in stable_fields):
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_INVALID,
                "existing round metadata conflicts with requested round",
                context={"path": str(meta_path)},
            )
        if execution_artifacts:
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_INVALID,
                "existing round contains execution artifacts; start a new round instead of reusing stale outputs",
                context={"round_dir": str(round_dir), "artifacts": execution_artifacts},
            )
    else:
        if execution_artifacts:
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_INVALID,
                "round directory contains execution artifacts without prepared metadata",
                context={"round_dir": str(round_dir), "artifacts": execution_artifacts},
            )
        write_json_atomic(meta_path, round_meta)
    write_json_atomic(round_dir / "commit_lineage.json", lineage)
    return round_dir


def _existing_execution_artifacts(round_dir: Path, eval_id: str) -> list[str]:
    candidates = [round_dir / name for name in ROUND_EXECUTION_ARTIFACTS]
    candidates.append(round_dir / f"{eval_id}.json")
    candidates.append(round_dir / eval_id)
    existing = []
    for path in candidates:
        if path.exists():
            existing.append(path.relative_to(round_dir).as_posix())
    return sorted(set(existing))


def build_commit_lineage(
    *,
    eval_code_commit: str,
    fix_commit: str | None,
    post_eval_review_fix_commit: str | None,
) -> dict[str, Any]:
    eval_commit = validate_commit(eval_code_commit, field="eval_code_commit", required=True)
    validated_fix = validate_commit(fix_commit, field="fix_commit", required=False)
    validated_post = validate_commit(
        post_eval_review_fix_commit,
        field="post_eval_review_fix_commit",
        required=False,
    )
    if validated_fix is not None and validated_fix != eval_commit:
        raise PtvRuntimeError(
            PtvErrorCode.COMMIT_LINEAGE_INVALID,
            "fix_commit must equal eval_code_commit for the code evaluated in this round",
            context={"fix_commit": validated_fix, "eval_code_commit": eval_commit},
        )
    if validated_post is not None and validated_post == eval_commit:
        raise PtvRuntimeError(
            PtvErrorCode.COMMIT_LINEAGE_INVALID,
            "post_eval_review_fix_commit must identify code created after the evaluated commit",
        )
    return {
        "schema_version": "metricrca-ptv-commit-lineage-v1",
        "eval_code_commit": eval_commit,
        "fix_commit": validated_fix,
        "post_eval_review_fix_commit": validated_post,
        "written_at": utc_now_iso(),
    }


def canonicalize_eval_artifacts(*, round_dir: Path, eval_id: str) -> dict[str, Path]:
    source_result = round_dir / f"{eval_id}.json"
    if not source_result.exists():
        alternate = round_dir / eval_id / "eval-result.json"
        if alternate.exists():
            source_result = alternate
        else:
            raise PtvRuntimeError(
                PtvErrorCode.EVAL_RESULT_MISSING,
                "eval runner result was not found",
                context={"expected": [str(round_dir / f'{eval_id}.json'), str(alternate)]},
            )
    payload = read_json(source_result)
    validate_eval_result(payload, expected_eval_id=eval_id)
    canonical_result = round_dir / "eval-result.json"
    write_json_atomic(canonical_result, payload)

    source_eval_dir = round_dir / eval_id
    canonical_cases = round_dir / "per-case"
    source_cases = source_eval_dir / "cases"
    if not source_cases.exists():
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "eval per-case artifact directory is required",
            context={"path": str(source_cases)},
        )
    case_ids = {str(row["case_id"]) for row in payload["cases"] if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)}
    case_files = {path.stem for path in source_cases.glob("*.json") if path.is_file()}
    missing = sorted(case_ids - case_files)
    extra = sorted(case_files - case_ids)
    if missing or extra:
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "per-case artifacts must exactly match eval cases",
            context={"missing_cases": missing, "extra_cases": extra},
        )
    if canonical_cases.exists():
        shutil.rmtree(canonical_cases)
    shutil.copytree(source_cases, canonical_cases)

    result = {"eval_result": canonical_result}
    result["per_case"] = canonical_cases
    nested_grpo = source_eval_dir / "grpo_dataset"
    if nested_grpo.exists():
        result["runner_grpo_dataset"] = nested_grpo
    return result


def validate_round_outputs_fresh(*, round_dir: Path, eval_id: str, barrier: Mapping[str, Any]) -> None:
    commands = barrier.get("commands")
    if barrier.get("status") != "reached" or not isinstance(commands, Mapping):
        raise PtvRuntimeError(PtvErrorCode.BARRIER_NOT_REACHED, "barrier must contain reached command results")
    _validate_command_freshness(
        commands,
        name="prediction",
        required_path=round_dir / "predictions.jsonl",
    )
    eval_source = round_dir / f"{eval_id}.json"
    alternate = round_dir / eval_id / "eval-result.json"
    if not eval_source.exists() and alternate.exists():
        eval_source = alternate
    _validate_command_freshness(commands, name="eval", required_path=eval_source)


def _validate_command_freshness(commands: Mapping[str, Any], *, name: str, required_path: Path) -> None:
    command = commands.get(name)
    if not isinstance(command, Mapping):
        raise PtvRuntimeError(PtvErrorCode.BARRIER_NOT_REACHED, "barrier missing command result", context={"name": name})
    if command.get("return_code") != 0:
        raise PtvRuntimeError(PtvErrorCode.BARRIER_NOT_REACHED, "barrier command did not succeed", context={"name": name})
    started_at = command.get("started_at")
    if not isinstance(started_at, str):
        raise PtvRuntimeError(PtvErrorCode.BARRIER_NOT_REACHED, "barrier command missing started_at", context={"name": name})
    if not required_path.exists():
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_MISSING,
            "required round artifact was not produced",
            context={"name": name, "path": str(required_path)},
        )
    started = _parse_utc(started_at).timestamp()
    if required_path.stat().st_mtime + 1e-6 < started:
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_INVALID,
            "round artifact is older than the command that should have produced it",
            context={"name": name, "path": str(required_path), "started_at": started_at},
        )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PtvRuntimeError(
            PtvErrorCode.BARRIER_NOT_REACHED,
            "barrier timestamp must be ISO-8601",
            context={"value": value},
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_eval_result(payload: Mapping[str, Any], *, expected_eval_id: str | None = None) -> None:
    eval_id = payload.get("eval_id")
    summary = payload.get("summary")
    cases = payload.get("cases")
    if not isinstance(eval_id, str) or not eval_id:
        raise PtvRuntimeError(PtvErrorCode.EVAL_RESULT_INVALID, "eval-result.json missing eval_id")
    if expected_eval_id is not None and eval_id != expected_eval_id:
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "eval-result eval_id does not match round eval_id",
            context={"expected": expected_eval_id, "actual": eval_id},
        )
    if not isinstance(summary, Mapping):
        raise PtvRuntimeError(PtvErrorCode.EVAL_RESULT_INVALID, "eval-result.json missing summary object")
    if not isinstance(cases, list):
        raise PtvRuntimeError(PtvErrorCode.EVAL_RESULT_INVALID, "eval-result.json missing cases array")
    case_total = summary.get("case_total")
    if not isinstance(case_total, int) or case_total != len(cases):
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "summary.case_total must equal the cases array length",
            context={"case_total": case_total, "actual_cases": len(cases)},
        )
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise PtvRuntimeError(
                PtvErrorCode.EVAL_RESULT_INVALID,
                "every eval case result must be an object",
                context={"index": index},
            )
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise PtvRuntimeError(
                PtvErrorCode.EVAL_RESULT_INVALID,
                "every eval case result must have a non-empty case_id",
                context={"index": index},
            )
        if case_id in seen:
            raise PtvRuntimeError(
                PtvErrorCode.EVAL_RESULT_INVALID,
                "duplicate case_id in eval result",
                context={"case_id": case_id},
            )
        seen.add(case_id)


def write_artifact_manifest(round_dir: Path) -> Path:
    entries: list[dict[str, Any]] = []
    for path in sorted(round_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        relative = path.relative_to(round_dir).as_posix()
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    manifest = {
        "schema_version": "metricrca-ptv-artifact-manifest-v1",
        "generated_at": utc_now_iso(),
        "artifact_count": len(entries),
        "artifacts": entries,
    }
    target = round_dir / "artifact_manifest.json"
    write_json_atomic(target, manifest)
    return target


def verify_artifact_manifest(round_dir: Path) -> dict[str, Any]:
    path = round_dir / "artifact_manifest.json"
    manifest = read_json(path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "artifact manifest missing artifacts list")
    for entry in artifacts:
        if not isinstance(entry, Mapping):
            raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "artifact manifest entry must be an object")
        relative = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise PtvRuntimeError(PtvErrorCode.ARTIFACT_INVALID, "artifact manifest entry is malformed")
        artifact_path = round_dir / relative
        if not artifact_path.exists():
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_MISSING,
                "artifact listed in manifest is missing",
                context={"path": str(artifact_path)},
            )
        actual_hash = file_sha256(artifact_path)
        if actual_hash != expected_hash:
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_INVALID,
                "artifact hash mismatch",
                context={"path": str(artifact_path), "expected": expected_hash, "actual": actual_hash},
            )
    return manifest


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_MISSING,
            "required JSON artifact is missing",
            context={"path": str(path)},
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PtvRuntimeError(
            PtvErrorCode.JSON_INVALID,
            "invalid JSON artifact",
            context={"path": str(path), "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(value, dict):
        raise PtvRuntimeError(
            PtvErrorCode.JSON_INVALID,
            "JSON artifact root must be an object",
            context={"path": str(path)},
        )
    return value


def read_jsonl(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        raise PtvRuntimeError(
            PtvErrorCode.ARTIFACT_MISSING,
            "required JSONL artifact is missing",
            context={"path": str(path)},
        )
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PtvRuntimeError(
                PtvErrorCode.JSONL_INVALID,
                "invalid JSONL row",
                context={"path": str(path), "line": line_number, "column": exc.colno},
            ) from exc
        if not isinstance(value, dict):
            raise PtvRuntimeError(
                PtvErrorCode.JSONL_INVALID,
                "JSONL row must be an object",
                context={"path": str(path), "line": line_number},
            )
        rows.append(value)
    if not rows and not allow_empty:
        raise PtvRuntimeError(
            PtvErrorCode.JSONL_INVALID,
            "JSONL artifact must contain at least one row",
            context={"path": str(path)},
        )
    return rows


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    write_text_atomic(path, text)


def write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    text = "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows)
    write_text_atomic(path, text)


def write_text_atomic(path: Path, text: str) -> None:
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
