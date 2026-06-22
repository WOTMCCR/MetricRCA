from __future__ import annotations

import os
import signal
import sys
from typing import Any

import pytest


os.environ.setdefault(
    "METRIC_RCA_DB_DSN",
    "mysql+pymysql://metric_rca_app:metric_rca_app@127.0.0.1:3307/metric_rca",
)
os.environ.setdefault(
    "METRIC_RCA_READONLY_DB_DSN",
    "mysql+pymysql://metric_rca_reader:metric_rca_reader@127.0.0.1:3307/metric_rca",
)
os.environ.setdefault("METRIC_RCA_LLM_MODEL", "gpt-test")
os.environ.setdefault("METRIC_RCA_LLM_API_KEY", "test-key")


def pytest_addoption(parser: Any) -> None:
    if "pytest_timeout" in sys.modules:
        return
    parser.addoption(
        "--timeout",
        action="store",
        default=None,
        type=float,
        help="Fail each test call if it exceeds N seconds.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: Any) -> Any:
    timeout = item.config.getoption("timeout", default=None)
    if timeout is None:
        yield
        return
    if not hasattr(signal, "SIGALRM"):
        raise RuntimeError("PYTEST_TIMEOUT_UNSUPPORTED: SIGALRM is required for --timeout")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, float(timeout))
    signal.signal(signal.SIGALRM, _timeout_handler)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError("PYTEST_TIMEOUT_EXCEEDED: test exceeded --timeout")
