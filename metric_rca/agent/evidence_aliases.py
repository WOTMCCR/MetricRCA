"""Shared evidence alias allocation for deterministic RCA plans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Sequence, TypeVar


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

_DIMENSION_TOKEN = {
    "channel": "ch",
    "category": "cat",
    "product": "prod",
    "device": "dev",
    "warehouse": "wh",
}

_SIGNAL_TOKEN = {
    "campaign": "campaign",
    "inventory": "inventory",
    "conversion": "conversion",
    "refund_quality": "refund",
    "interaction": "int",
}

_SELECTION_ALIAS_BY_LANE = {
    ("channel", "campaign"): "E_select_ch_campaign",
    ("channel", "conversion"): "E_select_channel_conv",
    ("channel", "interaction"): "E_select_channel_int",
    ("category", "interaction"): "E_select_category_int",
}

_SIGNAL_ALIAS_BY_LANE = {
    ("channel", "campaign"): "E3_ch_campaign",
    ("channel", "conversion"): "E3_ch_conversion",
    ("channel", "interaction"): "E3_ch_int",
    ("category", "interaction"): "E3_cat_int",
}

_CONTRIBUTION_ALIAS_BY_LANE = {
    ("channel", "campaign"): "E4_channel_campaign",
    ("channel", "conversion"): "E4_channel_conversion",
    ("channel", "interaction"): "E4_channel_int",
    ("category", "interaction"): "E4_category_int",
}


class DiscoveryLaneLike(Protocol):
    dimension: str
    signal_type: str
    element_binding: str
    element: str | None
    evidence_alias: str | None
    selection_alias: str | None
    signal_evidence_alias: str | None


LaneT = TypeVar("LaneT", bound=DiscoveryLaneLike)


@dataclass(frozen=True)
class LaneEvidenceAliases:
    """All persisted aliases owned by one discovery lane."""

    selection: str
    signal: str
    contribution: str



def e3_alias_for_dimension(dimension: str) -> str | None:
    return E3_ALIAS_BY_DIMENSION.get(dimension)



def e3_alias_for_signal_lane(dimension: str, signal_type: str, *, element_known: bool) -> str:
    dimension_alias = e3_alias_for_dimension(dimension) or f"E3_{dimension}"
    if not element_known:
        return dimension_alias
    return f"{dimension_alias}_{signal_type}"



def allocate_lane_aliases(
    *,
    dimension: str,
    signal_type: str,
    dimension_ordinal: int,
    element_known: bool,
) -> LaneEvidenceAliases:
    """Allocate stable aliases without coupling policy data to storage names.

    The first lane for a dimension owns the compact generic aliases. Additional
    lanes use signal-qualified aliases so multiple signal chains can coexist.
    """

    if dimension_ordinal < 0:
        raise ValueError("dimension_ordinal must be non-negative")

    if dimension_ordinal == 0:
        selection = f"E_select_{dimension}"
        signal = e3_alias_for_signal_lane(
            dimension,
            signal_type,
            element_known=element_known,
        )
        contribution = f"E4_{dimension}"
        return LaneEvidenceAliases(
            selection=selection,
            signal=signal,
            contribution=contribution,
        )

    lane_key = (dimension, signal_type)
    dimension_token = _DIMENSION_TOKEN.get(dimension, dimension[:8])
    signal_token = _SIGNAL_TOKEN.get(signal_type, signal_type[:10])
    return LaneEvidenceAliases(
        selection=_SELECTION_ALIAS_BY_LANE.get(
            lane_key,
            f"E_select_{dimension_token}_{signal_token}",
        ),
        signal=_SIGNAL_ALIAS_BY_LANE.get(
            lane_key,
            f"E3_{dimension_token}_{signal_token}",
        ),
        contribution=_CONTRIBUTION_ALIAS_BY_LANE.get(
            lane_key,
            f"E4_{dimension_token}_{signal_token}",
        ),
    )



def allocate_discovery_lane_aliases(lanes: Sequence[LaneT]) -> tuple[LaneT, ...]:
    """Return lanes with centrally allocated aliases and detect policy drift.

    Existing explicit alias fields are treated as compatibility assertions. A
    disagreement fails fast instead of allowing policy_registry and the runtime
    allocator to silently diverge.
    """

    dimension_counts: dict[str, int] = {}
    allocated_lanes: list[LaneT] = []

    for lane in lanes:
        ordinal = dimension_counts.get(lane.dimension, 0)
        dimension_counts[lane.dimension] = ordinal + 1
        element_known = lane.element_binding != "dynamic" and (
            lane.element_binding == "explicit_scope" or lane.element is not None
        )
        aliases = allocate_lane_aliases(
            dimension=lane.dimension,
            signal_type=lane.signal_type,
            dimension_ordinal=ordinal,
            element_known=element_known,
        )
        _validate_declared_alias("selection_alias", lane.selection_alias, aliases.selection)
        _validate_declared_alias("signal_evidence_alias", lane.signal_evidence_alias, aliases.signal)
        _validate_declared_alias("evidence_alias", lane.evidence_alias, aliases.contribution)
        allocated_lanes.append(
            replace(
                lane,
                selection_alias=aliases.selection,
                signal_evidence_alias=aliases.signal,
                evidence_alias=aliases.contribution,
            )
        )

    return tuple(allocated_lanes)



def e2_alias_for_e3_id(e3_id: str, *, run_id: str) -> str | None:
    alias = e3_id.removeprefix(f"{run_id}:")
    for e3_prefix, e2_alias in E3_ALIAS_TO_E2_ALIAS.items():
        if alias == e3_prefix or alias.startswith(f"{e3_prefix}_"):
            return e2_alias
    return None



def evidence_alias_fits(run_id: str, alias: str) -> bool:
    return len(f"{run_id}:{alias}") <= MAX_EVIDENCE_ID_LENGTH



def _validate_declared_alias(field: str, declared: str | None, allocated: str) -> None:
    if declared is not None and declared != allocated:
        raise ValueError(
            f"EVIDENCE_ALIAS_POLICY_DRIFT: {field}={declared} expected={allocated}"
        )
