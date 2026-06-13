from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_commands_match_makefile() -> None:
    readme = (ROOT / "README.md").read_text()
    for target in ["up", "seed", "api", "ui", "eval", "test"]:
        result = subprocess.run(
            ["make", "-n", target],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        command = [
            line
            for line in result.stdout.splitlines()
            if line and not line.startswith("export ") and not line.startswith("make[")
        ][-1]
        assert f"make {target}" in readme
        assert command.split()[0] in readme


def test_readme_endpoints_match_fastapi_routes() -> None:
    readme = (ROOT / "README.md").read_text()
    routes = (ROOT / "metric_rca" / "api" / "routes.py").read_text()
    for endpoint in [
        "/health",
        "/api/rca/runs",
        "/api/rca/runs/{run_id}",
        "/api/rca/runs/{run_id}/trace",
        "/api/rca/runs/{run_id}/evidence",
        "/api/rca/runs/{run_id}/sql-audit",
        "/api/rca/runs/{run_id}/tasks",
        "/api/rca/runs/{run_id}/memory",
        "/api/evals/run",
        "/api/evals/{eval_id}",
    ]:
        assert endpoint in readme
        assert endpoint in routes


def test_readme_error_codes_match_domain_or_api_error_models() -> None:
    readme = (ROOT / "README.md").read_text()
    for code in [
        "SYSTEM_TABLE_READ_FAILED",
        "REPORT_ARTIFACT_MISSING",
        "EVAL_GROUND_TRUTH_MISSING",
        "REFLECTION_REPAIR_FAILED",
        "SQL_GUARD_REJECTED",
        "SQL_EXECUTION_FAILED",
        "LLM_REQUIRED_UNAVAILABLE",
    ]:
        assert code in readme


def test_architecture_md_has_mermaid_and_matches_required_nodes() -> None:
    architecture = (ROOT / "docs" / "architecture.md").read_text()
    assert architecture.count("```mermaid") >= 7
    for component in [
        "RunOrchestrator",
        "deepagents expert",
        "GuardMiddleware",
        "registered MetricRCA tools",
        "Persisted Reflection",
        "report_projection",
        "memory_write",
        "finish_run",
        "run_rca",
    ]:
        assert component in architecture


def test_architecture_md_mentions_persisted_report_projection() -> None:
    architecture = (ROOT / "docs" / "architecture.md").read_text()
    assert "Persisted Report Reconstruction" in architecture
    assert "evidence E4 result_summary.selected_candidate" in architecture
    assert "numeric_claims bound to E4" in architecture


def test_final_compliance_has_rows_1_to_27_with_status_and_proof() -> None:
    compliance = (ROOT / "docs" / "final-compliance.md").read_text()
    rows = re.findall(r"^\| (\d+) \| (satisfied|partial|intentionally deferred|missing) \|", compliance, re.M)
    assert [int(row[0]) for row in rows] == list(range(1, 28))
    assert all(status == "satisfied" for _, status in rows)
    assert "Bounded SQL retry is intentionally deferred" in compliance


def test_docs_do_not_claim_unimplemented_streamlit_ui() -> None:
    docs = "\n".join(
        [
            (ROOT / "README.md").read_text(),
            (ROOT / "docs" / "MetricRCA.md").read_text(),
            (ROOT / "docs" / "architecture.md").read_text(),
            (ROOT / "docs" / "final-compliance.md").read_text(),
        ]
    )
    assert "Streamlit" not in docs
    assert "streamlit" not in docs
    assert "React/Vite" in docs
    assert "frontend/" in docs
    assert "npm run dev --prefix frontend" in docs


def test_metricrca_doc_uses_react_vite_not_streamlit() -> None:
    metric_rca = (ROOT / "docs" / "MetricRCA.md").read_text()

    assert "Streamlit" not in metric_rca
    assert "streamlit" not in metric_rca
    assert "React/Vite" in metric_rca
    assert "frontend/" in metric_rca
    assert "npm run dev --prefix frontend" in metric_rca
