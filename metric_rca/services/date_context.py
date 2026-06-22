"""Date-context rules for intent parsing."""

from __future__ import annotations

from datetime import date
import re

from metric_rca.services.metric_contracts import MetricServiceError


_CURRENT_OR_FUTURE_RELATIVE_DATE = re.compile(
    r"\b(?:today|tomorrow|currently|right now|real time|same day)\b"
)
_EXPLICIT_CALENDAR_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b(?:on|for)\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{1,2}(?:st|nd|rd|th)?)\b"
)
_TRUE_MULTI_DAY_RANGE = re.compile(
    r"\b(?:last|past)\s+\d+\s+(?:days?|weeks?|months?)\b|"
    r"\bover\s+(?:the\s+)?(?:last|past)\b|"
    r"\b(?:between|from)\b.+\b(?:and|to)\b|"
    r"\bsince\s+(?:\d{4}-\d{2}-\d{2}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|\d{1,2}(?:st|nd|rd|th)?)\b"
)
_PAST_RELATIVE_SINGLE_DATE = re.compile(
    r"\b(?:yesterday|two days ago|\d+\s+days ago|last business day|previous business day|"
    r"since the weekend|over the weekend)\b"
)
_GENERIC_ANOMALY_OR_CHANGE = re.compile(
    r"\b(?:abnormal|anomaly|anomalous|off|drop|decline|declined|declining|fall|fell|"
    r"below expectation|below normal|normal seasonal range|seasonal range|change|changed|"
    r"wrong|happening)\b"
)


def resolve_target_date_for_run_context(
    question: str,
    *,
    parsed_target_date: date,
    business_today: date,
    run_target_date: date | None,
) -> date:
    """Return the authoritative target date for a parsed single-date RCA intent."""

    normalized = _normalize_question(question)
    if _CURRENT_OR_FUTURE_RELATIVE_DATE.search(normalized):
        raise MetricServiceError("DATE_RANGE_INVALID", "current or future dates are not completed business dates")
    if run_target_date is None:
        return parsed_target_date
    if run_target_date >= business_today:
        raise MetricServiceError("DATE_RANGE_INVALID", "run_target_date must be before business_today")
    if parsed_target_date == run_target_date:
        return parsed_target_date
    if should_bind_run_target_date(question, run_target_date=run_target_date):
        return run_target_date
    return parsed_target_date


def should_retry_run_target_date_error(question: str, *, run_target_date: date | None) -> bool:
    return should_bind_run_target_date(question, run_target_date=run_target_date)


def should_bind_run_target_date(question: str, *, run_target_date: date | None) -> bool:
    if run_target_date is None:
        return False
    normalized = _normalize_question(question)
    if _CURRENT_OR_FUTURE_RELATIVE_DATE.search(normalized):
        return False
    if _EXPLICIT_CALENDAR_DATE.search(normalized):
        return False
    if _TRUE_MULTI_DAY_RANGE.search(normalized):
        return False
    return bool(_PAST_RELATIVE_SINGLE_DATE.search(normalized) or _GENERIC_ANOMALY_OR_CHANGE.search(normalized))


def _normalize_question(question: str) -> str:
    return " ".join(question.casefold().split())
