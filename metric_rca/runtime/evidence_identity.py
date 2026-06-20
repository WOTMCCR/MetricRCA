"""Evidence identity and alias allocation.

The persisted primary key is independent from the human-readable evidence
reference.  Runtime code still exposes ``run_id:alias`` for compatibility,
but every construction and parse goes through this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from metric_rca.data.schema_contract import (
    AGENT_RUN_ID_MAX_LENGTH,
    EVIDENCE_ALIAS_MAX_LENGTH,
    EVIDENCE_REFERENCE_MAX_LENGTH,
)

MAX_RUN_ID_LENGTH = AGENT_RUN_ID_MAX_LENGTH
MAX_EVIDENCE_ALIAS_LENGTH = EVIDENCE_ALIAS_MAX_LENGTH
MAX_EVIDENCE_ID_LENGTH = EVIDENCE_REFERENCE_MAX_LENGTH

_ALIAS_RE = re.compile(r"^E[A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_]+")

E3_ALIAS_BY_DIMENSION = {
    "channel": "E3_ch",
    "product": "E3_prod",
    "category": "E3_cat",
    "device": "E3_dev",
    "warehouse": "E3_wh",
}

E2_ALIAS_BY_DIMENSION = {
    dimension: f"E2_{dimension}" for dimension in E3_ALIAS_BY_DIMENSION
}

E3_ALIAS_TO_E2_ALIAS = {
    e3_alias: E2_ALIAS_BY_DIMENSION[dimension]
    for dimension, e3_alias in E3_ALIAS_BY_DIMENSION.items()
}

_ALIAS_SUFFIXES: dict[str, tuple[str, str, str]] = {
    "campaign": ("campaign", "campaign", "campaign"),
    "conversion": ("conv", "conversion", "conversion"),
    "interaction": ("int", "int", "int"),
    "inventory": ("inventory", "inventory", "inventory"),
    "refund_quality": ("refund_quality", "refund_quality", "refund_quality"),
}


class EvidenceIdentityError(ValueError):
    """Typed validation error raised before persistence or execution."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    run_id: str
    alias: str

    @property
    def evidence_id(self) -> str:
        return compose_evidence_id(self.run_id, self.alias)


@dataclass(frozen=True, slots=True)
class LaneEvidenceAliases:
    selection: str
    signal: str
    contribution: str


# Preserve the public aliases already emitted by the v3 runtime while moving
# their ownership out of policy_registry. New lane discriminators must be
# registered here so policy changes fail fast during plan compilation.
_LANE_ALIAS_OVERRIDES: dict[tuple[str, str], LaneEvidenceAliases] = {
    ("channel", "campaign"): LaneEvidenceAliases(
        selection="E_select_ch_campaign",
        signal="E3_ch_campaign",
        contribution="E4_channel_campaign",
    ),
    ("channel", "conversion"): LaneEvidenceAliases(
        selection="E_select_channel_conv",
        signal="E3_ch_conversion",
        contribution="E4_channel_conversion",
    ),
    ("channel", "interaction"): LaneEvidenceAliases(
        selection="E_select_channel_int",
        signal="E3_ch_int",
        contribution="E4_channel_int",
    ),
    ("category", "interaction"): LaneEvidenceAliases(
        selection="E_select_category_int",
        signal="E3_cat_int",
        contribution="E4_category_int",
    ),
}


def validate_run_id(run_id: str) -> str:
    value = str(run_id)
    if not value:
        raise EvidenceIdentityError("RUN_ID_INVALID", "run_id must not be empty")
    if ":" in value:
        raise EvidenceIdentityError("RUN_ID_INVALID", "run_id must not contain ':'")
    if len(value) > MAX_RUN_ID_LENGTH:
        raise EvidenceIdentityError(
            "RUN_ID_TOO_LONG",
            f"run_id length {len(value)} exceeds {MAX_RUN_ID_LENGTH}",
        )
    return value


def validate_evidence_alias(alias: str) -> str:
    value = str(alias)
    if not value:
        raise EvidenceIdentityError("EVIDENCE_ALIAS_INVALID", "evidence alias must not be empty")
    if len(value) > MAX_EVIDENCE_ALIAS_LENGTH:
        raise EvidenceIdentityError(
            "EVIDENCE_ALIAS_TOO_LONG",
            f"evidence alias length {len(value)} exceeds {MAX_EVIDENCE_ALIAS_LENGTH}",
        )
    if not _ALIAS_RE.fullmatch(value):
        raise EvidenceIdentityError(
            "EVIDENCE_ALIAS_INVALID",
            f"invalid evidence alias: {value}",
        )
    return value


def compose_evidence_id(run_id: str, alias: str) -> str:
    valid_run_id = validate_run_id(run_id)
    valid_alias = validate_evidence_alias(alias)
    evidence_id = f"{valid_run_id}:{valid_alias}"
    if len(evidence_id) > MAX_EVIDENCE_ID_LENGTH:
        raise EvidenceIdentityError(
            "EVIDENCE_ID_TOO_LONG",
            f"evidence_id length {len(evidence_id)} exceeds {MAX_EVIDENCE_ID_LENGTH}",
        )
    return evidence_id


def split_evidence_id(evidence_id: str) -> EvidenceIdentity:
    value = str(evidence_id)
    run_id, separator, alias = value.partition(":")
    if not separator or not run_id or not alias:
        raise EvidenceIdentityError(
            "EVIDENCE_ID_INVALID",
            "evidence_id must use the form '<run_id>:<alias>'",
        )
    identity = EvidenceIdentity(
        run_id=validate_run_id(run_id),
        alias=validate_evidence_alias(alias),
    )
    if identity.evidence_id != value:
        raise EvidenceIdentityError("EVIDENCE_ID_INVALID", "evidence_id is not canonical")
    return identity


def evidence_belongs_to_run(evidence_id: str, run_id: str) -> bool:
    try:
        return split_evidence_id(evidence_id).run_id == validate_run_id(run_id)
    except EvidenceIdentityError:
        return False


def alias_matches(actual_alias: str, required_alias: str) -> bool:
    actual = validate_evidence_alias(actual_alias)
    required = validate_evidence_alias(required_alias)
    return actual == required or actual.startswith(f"{required}_")


def alias_from_row(row: dict[str, Any]) -> str:
    raw_alias = row.get("alias")
    if raw_alias is not None:
        return validate_evidence_alias(str(raw_alias))
    raw_id = row.get("evidence_id")
    if raw_id is None:
        raise EvidenceIdentityError("EVIDENCE_ID_INVALID", "evidence row has no alias or evidence_id")
    return split_evidence_id(str(raw_id)).alias


def lane_evidence_aliases(dimension: str, discriminator: str | None = None) -> LaneEvidenceAliases:
    dimension_token = _token(dimension)
    e3_base = e3_alias_for_dimension(dimension_token) or f"E3_{dimension_token}"
    if discriminator is None:
        aliases = LaneEvidenceAliases(
            selection=f"E_select_{dimension_token}",
            signal=e3_base,
            contribution=f"E4_{dimension_token}",
        )
    else:
        override = _LANE_ALIAS_OVERRIDES.get((dimension_token, str(discriminator)))
        if override is not None:
            aliases = override
        else:
            selection_suffix, signal_suffix, contribution_suffix = _lane_suffixes(discriminator)
            aliases = LaneEvidenceAliases(
                selection=f"E_select_{dimension_token}_{selection_suffix}",
                signal=f"{e3_base}_{signal_suffix}",
                contribution=f"E4_{dimension_token}_{contribution_suffix}",
            )
    validate_evidence_alias(aliases.selection)
    validate_evidence_alias(aliases.signal)
    validate_evidence_alias(aliases.contribution)
    return aliases


def e3_alias_for_dimension(dimension: str) -> str | None:
    return E3_ALIAS_BY_DIMENSION.get(str(dimension))


def e3_alias_for_signal_lane(dimension: str, signal_type: str, *, element_known: bool) -> str:
    dimension_alias = e3_alias_for_dimension(dimension) or f"E3_{_token(dimension)}"
    if not element_known:
        return validate_evidence_alias(dimension_alias)
    return validate_evidence_alias(f"{dimension_alias}_{_token(signal_type)}")


def e2_alias_for_e3_alias(e3_alias: str) -> str | None:
    alias = validate_evidence_alias(e3_alias)
    for e3_prefix, e2_alias in E3_ALIAS_TO_E2_ALIAS.items():
        if alias == e3_prefix or alias.startswith(f"{e3_prefix}_"):
            return e2_alias
    return None


def e2_alias_for_e3_id(e3_id: str, *, run_id: str) -> str | None:
    identity = split_evidence_id(e3_id)
    if identity.run_id != validate_run_id(run_id):
        return None
    return e2_alias_for_e3_alias(identity.alias)


def evidence_alias_fits(run_id: str, alias: str) -> bool:
    try:
        compose_evidence_id(run_id, alias)
    except EvidenceIdentityError:
        return False
    return True


def _lane_suffixes(discriminator: str) -> tuple[str, str, str]:
    value = str(discriminator)
    configured = _ALIAS_SUFFIXES.get(value)
    if configured is not None:
        return configured
    raise EvidenceIdentityError(
        "EVIDENCE_ALIAS_DISCRIMINATOR_UNKNOWN",
        f"unknown evidence alias discriminator: {value}",
    )


def _token(value: str) -> str:
    token = _TOKEN_RE.sub("_", str(value).strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    if not token:
        raise EvidenceIdentityError("EVIDENCE_ALIAS_INVALID", "alias token must not be empty")
    return token
