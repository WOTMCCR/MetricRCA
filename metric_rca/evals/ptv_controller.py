"""Fail-fast process controller for the parallel PTV prediction/eval barrier."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Mapping, Sequence

from metric_rca.evals.ptv_artifacts import utc_now_iso, write_json_atomic
from metric_rca.evals.ptv_errors import PtvErrorCode, PtvRuntimeError


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    log_path: Path
    cwd: Path
    env: Mapping[str, str] | None = None

    @classmethod
    def from_shell_text(
        cls,
        *,
        name: str,
        command: str,
        log_path: Path,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> "CommandSpec":
        argv = tuple(shlex.split(command))
        if not argv:
            raise PtvRuntimeError(
                PtvErrorCode.COMMAND_INVALID,
                "command must contain at least one argument",
                context={"name": name},
            )
        return cls(name=name, argv=argv, log_path=log_path, cwd=cwd, env=env)


@dataclass(frozen=True)
class CommandResult:
    name: str
    argv: tuple[str, ...]
    return_code: int
    started_at: str
    finished_at: str
    log_path: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "return_code": self.return_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_path": self.log_path,
        }


@dataclass
class _RunningCommand:
    spec: CommandSpec
    process: subprocess.Popen[str]
    log_handle: object
    started_at: str


def run_parallel_prediction_and_eval(
    *,
    prediction: CommandSpec,
    evaluation: CommandSpec,
    barrier_path: Path,
    poll_interval_seconds: float = 0.05,
) -> dict[str, object]:
    if prediction.name == evaluation.name:
        raise PtvRuntimeError(PtvErrorCode.COMMAND_INVALID, "parallel command names must be unique")
    running: list[_RunningCommand] = []
    try:
        running.append(_start(prediction))
        running.append(_start(evaluation))
    except PtvRuntimeError:
        for item in running:
            _terminate(item)
            item.log_handle.close()
        raise
    results: dict[str, CommandResult] = {}
    failed: CommandResult | None = None
    while running:
        for item in list(running):
            return_code = item.process.poll()
            if return_code is None:
                continue
            result = _finish(item, return_code)
            results[result.name] = result
            running.remove(item)
            if return_code != 0 and failed is None:
                failed = result
                for peer in running:
                    _terminate(peer)
        if failed is not None:
            for peer in list(running):
                return_code = peer.process.wait(timeout=10)
                result = _finish(peer, return_code)
                results[result.name] = result
                running.remove(peer)
            raise PtvRuntimeError(
                PtvErrorCode.PARALLEL_STAGE_FAILED,
                "prediction/eval parallel stage failed; peer process was terminated",
                context={"failed": failed.as_dict(), "results": {key: value.as_dict() for key, value in results.items()}},
            )
        if running:
            time.sleep(poll_interval_seconds)
    payload = {
        "schema_version": "metricrca-ptv-barrier-v1",
        "reached_at": utc_now_iso(),
        "status": "reached",
        "commands": {key: value.as_dict() for key, value in sorted(results.items())},
    }
    write_json_atomic(barrier_path, payload)
    return payload


def run_checked_command(spec: CommandSpec) -> CommandResult:
    running = _start(spec)
    return_code = running.process.wait()
    result = _finish(running, return_code)
    if return_code != 0:
        raise PtvRuntimeError(
            PtvErrorCode.COMMAND_FAILED,
            "PTV command failed",
            context=result.as_dict(),
        )
    return result


def git_head(repo_root: Path) -> str:
    return _git_value(repo_root, ("rev-parse", "HEAD"))


def git_branch(repo_root: Path) -> str:
    return _git_value(repo_root, ("branch", "--show-current"))


def _git_value(repo_root: Path, args: Sequence[str]) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise PtvRuntimeError(
            PtvErrorCode.GIT_COMMAND_FAILED,
            "git command failed",
            context={"argv": ["git", *args], "stderr": completed.stderr.strip()},
        )
    value = completed.stdout.strip()
    if not value:
        raise PtvRuntimeError(
            PtvErrorCode.GIT_COMMAND_FAILED,
            "git command returned an empty value",
            context={"argv": ["git", *args]},
        )
    return value


def _start(spec: CommandSpec) -> _RunningCommand:
    if not spec.argv:
        raise PtvRuntimeError(PtvErrorCode.COMMAND_INVALID, "command argv must not be empty")
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    spec.cwd.resolve(strict=True)
    log_handle = spec.log_path.open("w", encoding="utf-8")
    environment = dict(os.environ)
    if spec.env is not None:
        environment.update({str(key): str(value) for key, value in spec.env.items()})
    started_at = utc_now_iso()
    argv = _resolve_executable(spec.argv)
    try:
        process = subprocess.Popen(
            argv,
            cwd=spec.cwd,
            env=environment,
            text=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.close()
        raise PtvRuntimeError(
            PtvErrorCode.COMMAND_FAILED,
            "failed to start PTV command",
            context={"name": spec.name, "argv": list(argv), "cwd": str(spec.cwd), "error": str(exc)},
        ) from exc
    return _RunningCommand(spec=spec, process=process, log_handle=log_handle, started_at=started_at)


def _resolve_executable(argv: tuple[str, ...]) -> tuple[str, ...]:
    executable = argv[0]
    if executable == sys.executable:
        return (str(Path(sys.executable).resolve()), *argv[1:])
    executable_path = Path(executable)
    if executable_path.is_absolute() or executable_path.parent == Path("."):
        return argv
    repo_relative = Path.cwd() / executable_path
    if repo_relative.exists():
        return (str(repo_relative.resolve()), *argv[1:])
    return argv


def _terminate(item: _RunningCommand) -> None:
    if item.process.poll() is not None:
        return
    item.process.terminate()
    try:
        item.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        item.process.kill()
        item.process.wait(timeout=5)


def _finish(item: _RunningCommand, return_code: int) -> CommandResult:
    item.log_handle.close()
    return CommandResult(
        name=item.spec.name,
        argv=item.spec.argv,
        return_code=return_code,
        started_at=item.started_at,
        finished_at=utc_now_iso(),
        log_path=str(item.spec.log_path),
    )
