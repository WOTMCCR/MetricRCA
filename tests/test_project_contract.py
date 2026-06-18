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
        "seed": (
            "METRIC_RCA_DATA_SEED=20260606 METRIC_RCA_SEED_PROFILE=regression "
            "METRIC_RCA_ALLOW_DESTRUCTIVE_SEED=false python -m metric_rca.data.seed_data"
        ),
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


def test_makefile_seed_profile_and_destructive_flags_are_explicit() -> None:
    result = subprocess.run(
        ["make", "-n", "seed", "SEED_PROFILE=acceptance", "ALLOW_DESTRUCTIVE_SEED=true"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert (
        "METRIC_RCA_SEED_PROFILE=acceptance METRIC_RCA_ALLOW_DESTRUCTIVE_SEED=true"
        in result.stdout
    )


def test_makefile_declares_v3_eval_suite_targets() -> None:
    expected = {
        "eval-regression": "METRIC_RCA_EVAL_SUITE=regression python -m metric_rca.evals.runner",
        "eval-blind": "METRIC_RCA_EVAL_SUITE=blind python -m metric_rca.evals.runner",
        "eval-seed-sweep": "METRIC_RCA_EVAL_SUITE=seed-sweep python -m metric_rca.evals.runner",
        "eval-mutation": "METRIC_RCA_EVAL_SUITE=mutation python -m metric_rca.evals.runner",
        "eval-memory-treatment": "METRIC_RCA_EVAL_SUITE=memory-treatment python -m metric_rca.evals.runner",
        "eval-acceptance": "METRIC_RCA_EVAL_SUITE=acceptance python -m metric_rca.evals.runner",
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


def test_active_docs_do_not_claim_legacy_deepagents_runtime_or_20_case_only_eval() -> None:
    active_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "architecture.md",
        ROOT / "docs" / "COMPLIANCE_MATRIX.md",
        ROOT / "docs" / "final-design" / "06-v3-repair-plan.md",
    ]
    forbidden = [
        "deepagents runtime",
        "llm free tool-calling",
        "old react loop",
        "20-case eval",
        "20 case eval",
        "20-case-only",
    ]
    for path in active_docs:
        source = path.read_text().lower()
        assert not any(token in source for token in forbidden), path


def test_historical_final_design_files_are_marked_superseded() -> None:
    for path in sorted((ROOT / "docs" / "final-design").glob("0[0-5]-*.md")):
        source = path.read_text().lower()
        assert "historical v2 design" in source
        assert "06-v3-repair-plan.md" in source


def test_runner_entrypoint_has_no_legacy_runtime_memory_or_repair_methods() -> None:
    source = (ROOT / "metric_rca" / "agent" / "runner.py").read_text()
    forbidden = [
        "_read_required_memory",
        "_write_required_memory",
        "_write_reflection_memory",
        "_repair_instruction",
        "Repair Reflection issue using persisted evidence only",
    ]
    assert not any(token in source for token in forbidden)


def test_eval_stream_make_target_passes_eval_id_and_output_dir() -> None:
    result = subprocess.run(
        ["make", "-n", "eval-stream", "EVAL_ID=eval-predict-test", "EVAL_OUTPUT_DIR=eval_out/ptv/cycle-test"],
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
    assert lines[-1] == (
        "python -m metric_rca.evals.runner --stream --output-dir eval_out/ptv/cycle-test "
        "--eval-id eval-predict-test"
    )


def test_eval_gaps_make_target_passes_eval_id_and_output_dir() -> None:
    result = subprocess.run(
        ["make", "-n", "eval-gaps", "EVAL_ID=eval-predict-test", "EVAL_OUTPUT_DIR=eval_out/ptv/cycle-test"],
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
    assert lines[-1] == (
        "python -m metric_rca.evals.gap_analyzer --output-dir eval_out/ptv/cycle-test "
        "--eval-id eval-predict-test"
    )


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
