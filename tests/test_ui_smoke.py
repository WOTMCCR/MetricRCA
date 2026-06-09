from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_react_ui_tests_pass() -> None:
    result = subprocess.run(
        ["npm", "test", "--prefix", str(FRONTEND), "--", "--run"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "passed" in result.stdout


def test_ui_import_path_does_not_bypass_api() -> None:
    offenders = []
    for path in (FRONTEND / "src").rglob("*.ts*"):
        text = path.read_text()
        if "metric_rca/agent" in text or "metric_rca.agent" in text or "run_rca" in text:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_react_ui_uses_browser_fetch_instead_of_python_proxy_path() -> None:
    api_client = (FRONTEND / "src" / "apiClient.ts").read_text()

    assert "fetch(" in api_client
    assert "httpx" not in api_client
    assert "trust_env" not in api_client
