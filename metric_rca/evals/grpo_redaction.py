"""Recursive secret redaction for exported GRPO records."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping


_REDACTED = "<REDACTED>"
_SECRET_FIELD_RE = re.compile(
    r"^(?:.*[_-])?(?:token|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|client[_-]?secret|private[_-]?key)$",
    re.IGNORECASE,
)
_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE),
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----", re.DOTALL),
)
_DSN_CREDENTIAL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|auth[_-]?token|session[_-]?token|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|client[_-]?secret)\b\s*[:=]\s*['\"]?([^\s,'\"}]+)"
)
_EMPTY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|auth[_-]?token|session[_-]?token|api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|client[_-]?secret)\b\s*[:=]\s*(?=(?:\s|[,'\"}]|$))"
)


class GrpoRedactionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RedactionResult:
    value: Any
    redaction_count: int


def redact_record(value: Any) -> RedactionResult:
    redacted, count = _redact(value, key=None)
    assert_no_secrets(redacted)
    return RedactionResult(value=redacted, redaction_count=count)


def assert_no_secrets(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    for pattern in _PATTERNS:
        if pattern.search(serialized):
            raise GrpoRedactionError("GRPO_SECRET_REMAINS", "secret-like token remains after redaction")
    scan_value = serialized.replace(_REDACTED, "")
    scan_value = _EMPTY_ASSIGNMENT_RE.sub("", scan_value)
    if _DSN_CREDENTIAL_RE.search(scan_value) or _ASSIGNMENT_RE.search(scan_value):
        raise GrpoRedactionError("GRPO_SECRET_REMAINS", "credential-like assignment remains after redaction")


def _redact(value: Any, *, key: str | None) -> tuple[Any, int]:
    if key is not None and _SECRET_FIELD_RE.fullmatch(key) and value not in {None, ""}:
        return _REDACTED, 1
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        count = 0
        for child_key, child_value in value.items():
            redacted, child_count = _redact(child_value, key=str(child_key))
            output[str(child_key)] = redacted
            count += child_count
        return output, count
    if isinstance(value, list):
        output_list = []
        count = 0
        for child in value:
            redacted, child_count = _redact(child, key=None)
            output_list.append(redacted)
            count += child_count
        return output_list, count
    if isinstance(value, tuple):
        output_tuple = []
        count = 0
        for child in value:
            redacted, child_count = _redact(child, key=None)
            output_tuple.append(redacted)
            count += child_count
        return output_tuple, count
    if not isinstance(value, str):
        return value, 0
    text = value
    count = 0
    text, replacements = _DSN_CREDENTIAL_RE.subn(r"\1<REDACTED>\3", text)
    count += replacements
    text, replacements = _ASSIGNMENT_RE.subn(lambda match: f"{match.group(1)}={_REDACTED}", text)
    count += replacements
    for pattern in _PATTERNS:
        text, replacements = pattern.subn(_REDACTED, text)
        count += replacements
    return text, count
