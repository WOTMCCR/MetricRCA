from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIRS = [ROOT / "metric_rca" / "services", ROOT / "metric_rca" / "agent"]


def _runtime_sources() -> list[Path]:
    return [
        path
        for directory in RUNTIME_DIRS
        if directory.exists()
        for path in directory.rglob("*.py")
    ]


def test_runtime_code_does_not_read_anomaly_ground_truth() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _runtime_sources()
        if "anomaly_ground_truth" in path.read_text()
    ]
    assert offenders == []


def test_services_do_not_import_db_or_repository_modules() -> None:
    forbidden = ["MetricRepository", "SQLRenderer", "SQLGuard", "create_engine", "pandas.read_sql", "pymysql"]
    offenders: list[str] = []
    for path in (ROOT / "metric_rca" / "services").glob("*.py"):
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_tools_and_services_have_no_direct_sql_or_broad_continue_fallbacks() -> None:
    forbidden = ["read_sql", "create_engine", "pymysql", ".execute(", "except Exception", "continue"]
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_services_and_tools_have_no_hardcoded_metric_definitions() -> None:
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        if "METRIC_DEFINITIONS" in source:
            offenders.append(f"{path.relative_to(ROOT)}:METRIC_DEFINITIONS")
        if "MetricDefinition(" in source:
            offenders.append(f"{path.relative_to(ROOT)}:MetricDefinition(")
    assert offenders == []


def test_services_and_tools_have_no_hardcoded_schema_context() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in _runtime_sources()
        if "SCHEMA_CONTEXT" in path.read_text()
    ]
    assert offenders == []


def test_services_and_tools_have_no_hardcoded_dimension_values() -> None:
    forbidden = [
        "_CHANNELS",
        "_CATEGORIES",
        "paid_ads",
        "organic",
        "affiliate",
        "electronics",
        "fashion",
        "home",
    ]
    offenders: list[str] = []
    for path in _runtime_sources():
        source = path.read_text()
        for token in forbidden:
            if token in source:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_gmv_decomposition_does_not_reference_order_count_or_pay_orders() -> None:
    source = (ROOT / "metric_rca" / "services" / "attribution_service.py").read_text()
    assert "pay_orders" not in source
    assert "order_count" not in source
