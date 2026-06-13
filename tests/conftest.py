from __future__ import annotations

import os


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
