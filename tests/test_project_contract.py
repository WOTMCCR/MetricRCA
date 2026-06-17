from __future__ import annotations

import json
import subprocess
import tomllib
from importlib.metadata import metadata, version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_current_phase_dependencies() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["name"] == "metric_rca"
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"

    declared = {
        dep.split("==", maxsplit=1)[0].split(">", maxsplit=1)[0].split("<", maxsplit=1)[0].strip(): dep
        for dep in pyproject["project"]["dependencies"]
    }
    required = {
        "pydantic",
        "pydantic-settings",
        "sqlalchemy",
        "pymysql",
        "sqlglot",
        "pandas",
        "openai-agents",
        "fastapi",
        "uvicorn",
        "httpx[socks]",
        "pytest",
    }
    forbidden_phase_gt1 = {
        "streamlit",
        "scikit-learn",
    }
    assert set(declared) == required
    assert forbidden_phase_gt1.isdisjoint(declared)
    assert declared["openai-agents"] == "openai-agents==0.17.5"

    installed = metadata("metric_rca")
    assert installed["Name"] == "metric_rca"
    installed_requires = {
        dep.split("==", maxsplit=1)[0].split(">", maxsplit=1)[0].split("<", maxsplit=1)[0].strip()
        for dep in installed.get_all("Requires-Dist") or []
    }
    assert installed_requires <= required
    assert version("pydantic").split(".", maxsplit=1)[0] == "2"
    assert int(version("sqlalchemy").split(".", maxsplit=1)[0]) >= 2


def test_makefile_targets_match_documented_commands() -> None:
    expected = {
        "up": "docker compose up -d mysql",
        "seed": "METRIC_RCA_DATA_SEED=20260606 python -m metric_rca.data.seed_data",
        "api": "uvicorn metric_rca.api.main:app --reload",
        "ui": "npm run dev --prefix frontend",
        "eval": "python -m metric_rca.evals.runner",
        "test": "pytest -q",
    }
    for target, command in expected.items():
        result = subprocess.run(
            ["make", "-n", target],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        lines = [
            line
            for line in result.stdout.splitlines()
            if line and not line.startswith("export ") and not line.startswith("make[")
        ]
        assert lines[-1] == command
    makefile = (ROOT / "Makefile").read_text()
    assert "streamlit" not in makefile


def test_eval_stream_make_target_passes_eval_id() -> None:
    result = subprocess.run(
        ["make", "-n", "eval-stream", "EVAL_ID=eval-predict-test"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    lines = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith("export ") and not line.startswith("make[")
    ]
    assert lines[-1] == "python -m metric_rca.evals.runner --stream --eval-id eval-predict-test"


def test_compose_declares_mysql_only_contract() -> None:
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]
    assert set(services) == {"mysql"}
    assert services["mysql"]["image"].startswith("mysql:8")
    assert any(
        "metric_rca/data/schema.sql" in volume.get("source", "")
        for volume in services["mysql"]["volumes"]
    )
    assert "healthcheck" in services["mysql"]


def test_core_runtime_depends_on_agent_runtime_not_openai_sdk() -> None:
    forbidden = [
        "from agents",
        "import agents",
        "OpenAIProvider",
        "RunConfig",
        "Runner",
        "Agent(",
    ]
    checked = [
        ROOT / "metric_rca" / "agent" / "runner.py",
        ROOT / "metric_rca" / "runtime" / "run_service.py",
        ROOT / "metric_rca" / "runtime" / "plan_compiler.py",
        ROOT / "metric_rca" / "runtime" / "plan_executor.py",
        ROOT / "metric_rca" / "runtime" / "sdk_tools.py",
        ROOT / "metric_rca" / "services" / "intent_planner.py",
        ROOT / "metric_rca" / "services" / "llm_client.py",
    ]
    for path in checked:
        source = path.read_text()
        assert not any(token in source for token in forbidden), path

    adapter = ROOT / "metric_rca" / "intelligence" / "openai_agents_runtime.py"
    assert "from agents" in adapter.read_text()
