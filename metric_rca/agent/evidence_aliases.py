"""Shared evidence alias helpers for E2/E3-family chains."""

from __future__ import annotations


E3_ALIAS_BY_DIMENSION = {
    "channel": "E3_ch",
    "product": "E3_prod",
    "category": "E3_cat",
    "device": "E3_dev",
    "warehouse": "E3_wh",
}

E2_ALIAS_BY_DIMENSION = {
    dimension: f"E2_{dimension}"
    for dimension in E3_ALIAS_BY_DIMENSION
}

E3_ALIAS_TO_E2_ALIAS = {
    e3_alias: E2_ALIAS_BY_DIMENSION[dimension]
    for dimension, e3_alias in E3_ALIAS_BY_DIMENSION.items()
}

MAX_EVIDENCE_ID_LENGTH = 64


def e3_alias_for_dimension(dimension: str) -> str | None:
    return E3_ALIAS_BY_DIMENSION.get(dimension)


def e3_alias_for_signal_lane(dimension: str, signal_type: str, *, element_known: bool) -> str:
    dimension_alias = e3_alias_for_dimension(dimension) or f"E3_{dimension}"
    if not element_known:
        return dimension_alias
    return f"{dimension_alias}_{signal_type}"


def e2_alias_for_e3_id(e3_id: str, *, run_id: str) -> str | None:
    alias = e3_id.removeprefix(f"{run_id}:")
    for e3_prefix, e2_alias in E3_ALIAS_TO_E2_ALIAS.items():
        if alias == e3_prefix or alias.startswith(f"{e3_prefix}_"):
            return e2_alias
    return None


def evidence_alias_fits(run_id: str, alias: str) -> bool:
    return len(f"{run_id}:{alias}") <= MAX_EVIDENCE_ID_LENGTH
