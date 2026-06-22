from __future__ import annotations

import json

from metric_rca.evals.grpo_redaction import redact_record


def test_recursive_redaction_removes_tokens_passwords_and_dsn_credentials() -> None:
    source = {
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456",
        "db_password": "hunter2",
        "nested": {
            "authorization": "Bearer abc.def.ghi",
            "session_token": "session-secret",
            "dsn": "mysql+pymysql://user:secret-password@127.0.0.1:3307/db",
            "text": "password=hunter2 token=inline-secret",
        },
    }
    result = redact_record(source)
    serialized = json.dumps(result.value)
    assert "abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "secret-password" not in serialized
    assert "hunter2" not in serialized
    assert "session-secret" not in serialized
    assert "inline-secret" not in serialized
    assert result.redaction_count >= 4
