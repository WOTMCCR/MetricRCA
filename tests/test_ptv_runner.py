from __future__ import annotations

from pathlib import Path
import sys

import pytest

from metric_rca.evals.ptv_controller import CommandSpec, run_parallel_prediction_and_eval
from metric_rca.evals.ptv_errors import PtvRuntimeError
from metric_rca.evals.ptv_runner import analyze_round


def test_parallel_controller_writes_barrier_only_after_both_commands_succeed(tmp_path: Path) -> None:
    prediction = CommandSpec(
        name="prediction",
        argv=(sys.executable, "-c", "from pathlib import Path; Path('prediction.done').write_text('ok')"),
        log_path=tmp_path / "prediction.log",
        cwd=tmp_path,
    )
    evaluation = CommandSpec(
        name="eval",
        argv=(sys.executable, "-c", "from pathlib import Path; Path('eval.done').write_text('ok')"),
        log_path=tmp_path / "eval.log",
        cwd=tmp_path,
    )
    barrier = tmp_path / "barrier.json"
    payload = run_parallel_prediction_and_eval(prediction=prediction, evaluation=evaluation, barrier_path=barrier)
    assert payload["status"] == "reached"
    assert barrier.exists()
    assert (tmp_path / "prediction.done").exists()
    assert (tmp_path / "eval.done").exists()


def test_parallel_controller_fails_fast_when_one_command_fails(tmp_path: Path) -> None:
    prediction = CommandSpec(
        name="prediction",
        argv=(sys.executable, "-c", "raise SystemExit(3)"),
        log_path=tmp_path / "prediction.log",
        cwd=tmp_path,
    )
    evaluation = CommandSpec(
        name="eval",
        argv=(sys.executable, "-c", "import time; time.sleep(10)"),
        log_path=tmp_path / "eval.log",
        cwd=tmp_path,
    )
    barrier = tmp_path / "barrier.json"
    with pytest.raises(PtvRuntimeError) as exc_info:
        run_parallel_prediction_and_eval(prediction=prediction, evaluation=evaluation, barrier_path=barrier)
    assert exc_info.value.code == "PTV_PARALLEL_STAGE_FAILED"
    assert not barrier.exists()


def test_analyze_round_requires_prepared_round_meta(tmp_path: Path) -> None:
    round_dir = tmp_path / "round-01"
    round_dir.mkdir()

    with pytest.raises(PtvRuntimeError) as exc_info:
        analyze_round(round_dir=round_dir, eval_id="eval-r1")

    assert exc_info.value.code == "PTV_ARTIFACT_MISSING"
