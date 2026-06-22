"""Evidence-bounded attribution experience playbooks.

The experience layer is deliberately non-authoritative. It may order existing
discovery lanes and describe evidence branches, but it cannot add a lane outside
the metric policy, bind a concrete dimension element, or score a final root
cause. Current-run evidence remains the only conclusion source.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
import re
from typing import Any, Literal, Sequence

from pydantic import Field, model_validator
import yaml

from metric_rca.business.policy_registry import DiscoveryLane
from metric_rca.domain.models import StrictModel
from metric_rca.services.metric_contracts import ParsedIntent

ExperienceSignalType = Literal[
    "campaign",
    "inventory",
    "conversion",
    "refund_quality",
    "interaction",
]
BranchObservation = Literal[
    "signal_anomaly",
    "factor_dominance",
    "cross_dimension_support",
    "explained_share",
    "residual_share",
]
BranchOperator = Literal["eq", "gte", "lte", "gt", "lt"]

_BANNED_CONFIG_KEYS = frozenset(
    {
        "answer",
        "answers",
        "case",
        "case_id",
        "case_ids",
        "element",
        "elements",
        "evidence_id",
        "evidence_ids",
        "expected",
        "expected_candidate",
        "expected_root_cause",
        "expected_root_causes",
        "expected_top1",
        "final_answer",
        "final_candidate",
        "selected_candidate",
        "target_date",
    }
)
_BANNED_TEXT_PATTERNS = (
    re.compile(r"(?:^|[^A-Za-z0-9])(?:C|MC|IX|RS)\d{2}[_-]", re.IGNORECASE),
    re.compile(r"\beval_out\b", re.IGNORECASE),
    re.compile(r"\bground[_ -]?truth\b", re.IGNORECASE),
    re.compile(r"\bprivate[_ -]?truth\b", re.IGNORECASE),
)


class AttributionExperienceError(ValueError):
    """Typed failure for invalid experience configuration."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ExperienceLaneRef(StrictModel):
    """A generic evidence lane; concrete business elements are forbidden."""

    dimension: str
    signal_type: ExperienceSignalType
    alias_discriminator: str | None = None

    def key(self) -> tuple[str, str, str | None]:
        return (self.dimension, self.signal_type, self.alias_discriminator)


class EvidenceBranch(StrictModel):
    """A typed advisory branch evaluated only against current-run evidence."""

    observation: BranchObservation
    operator: BranchOperator
    value: bool | float | str
    next_hypotheses: list[str] = Field(default_factory=list)


class AttributionHypothesis(StrictModel):
    """Business-mechanism hypothesis without a concrete answer binding."""

    hypothesis_id: str
    root_cause_types: list[str] = Field(default_factory=list)
    evidence_lanes: list[ExperienceLaneRef] = Field(default_factory=list)
    factor_graphs: list[str] = Field(default_factory=list)
    continue_when: list[EvidenceBranch] = Field(default_factory=list)
    deprioritize_when: list[EvidenceBranch] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)


class CandidateRetentionGuide(StrictModel):
    """Advisory candidate-retention policy for downstream merge evolution."""

    minimum_material_contribution: float = Field(default=0.20, ge=0.0, le=1.0)
    weak_signal_severity_floor: float = Field(default=0.10, ge=0.0, le=1.0)
    preserve_root_cause_diversity: bool = True
    preserve_cross_dimension_interaction: bool = True
    max_candidates: int = Field(default=6, ge=1, le=20)


class ResidualGapGuide(StrictModel):
    """Generic convergence bounds; they are not case-specific target weights."""

    continue_below_explained_share: float = Field(default=0.85, ge=0.0, le=1.0)
    converge_at_explained_share: float = Field(default=0.90, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> "ResidualGapGuide":
        if self.continue_below_explained_share > self.converge_at_explained_share:
            raise ValueError("EXPERIENCE_POLICY_INVALID")
        return self


class AttributionPlaybook(StrictModel):
    """Metric/family playbook that can only reference generic evidence lanes."""

    playbook_id: str
    metric_ids: list[str]
    question_families: list[str] = Field(default_factory=list)
    lane_priority: list[ExperienceLaneRef] = Field(default_factory=list)
    hypotheses: list[AttributionHypothesis] = Field(default_factory=list)
    retention: CandidateRetentionGuide = Field(default_factory=CandidateRetentionGuide)
    residual: ResidualGapGuide = Field(default_factory=ResidualGapGuide)


class AttributionPlaybookCatalog(StrictModel):
    version: int = Field(ge=1)
    playbooks: list[AttributionPlaybook]

    @model_validator(mode="after")
    def _unique_resolution_keys(self) -> "AttributionPlaybookCatalog":
        seen: set[tuple[str, str | None]] = set()
        for playbook in self.playbooks:
            families: list[str | None] = playbook.question_families or [None]
            for metric_id in playbook.metric_ids:
                for family in families:
                    key = (metric_id, family)
                    if key in seen:
                        raise ValueError("EXPERIENCE_POLICY_AMBIGUOUS")
                    seen.add(key)
        return self


class AttributionExperienceAdvice(StrictModel):
    """Auditable output consumed by the compiler, never by final scoring."""

    playbook_id: str
    hypotheses: list[AttributionHypothesis] = Field(default_factory=list)
    execution_lane_priority: list[ExperienceLaneRef] = Field(default_factory=list)
    required_lanes: list[ExperienceLaneRef] = Field(default_factory=list)
    retention: CandidateRetentionGuide = Field(default_factory=CandidateRetentionGuide)
    residual: ResidualGapGuide = Field(default_factory=ResidualGapGuide)
    memory_mode: Literal["disabled", "priority_only"] = "disabled"
    source_memory_ids: list[str] = Field(default_factory=list)


class AttributionExperienceCatalog:
    """Strict YAML-backed catalog with an anti-answer-library boundary."""

    def __init__(self, catalog: AttributionPlaybookCatalog) -> None:
        self._catalog = catalog

    @classmethod
    def from_mapping(cls, raw: Any) -> "AttributionExperienceCatalog":
        _assert_non_answer_bearing(raw)
        try:
            catalog = AttributionPlaybookCatalog.model_validate(raw)
        except ValueError as exc:
            raise AttributionExperienceError("EXPERIENCE_POLICY_INVALID", str(exc)) from exc
        return cls(catalog)

    @classmethod
    @lru_cache(maxsize=1)
    def load_default(cls) -> "AttributionExperienceCatalog":
        resource = files("metric_rca.business").joinpath("attribution_playbooks.yaml")
        try:
            raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise AttributionExperienceError("EXPERIENCE_POLICY_LOAD_FAILED", str(exc)) from exc
        return cls.from_mapping(raw)

    def resolve(self, *, metric_id: str, question_family: str) -> AttributionPlaybook | None:
        exact = [
            playbook
            for playbook in self._catalog.playbooks
            if metric_id in playbook.metric_ids and question_family in playbook.question_families
        ]
        if len(exact) > 1:
            raise AttributionExperienceError("EXPERIENCE_POLICY_AMBIGUOUS", f"{metric_id}/{question_family}")
        if exact:
            return exact[0]
        defaults = [
            playbook
            for playbook in self._catalog.playbooks
            if metric_id in playbook.metric_ids and not playbook.question_families
        ]
        if len(defaults) > 1:
            raise AttributionExperienceError("EXPERIENCE_POLICY_AMBIGUOUS", f"{metric_id}/default")
        return defaults[0] if defaults else None


class AttributionExperienceAdvisor:
    """Produces priority-only advice over policy-approved discovery lanes."""

    def __init__(self, *, catalog: AttributionExperienceCatalog | None = None) -> None:
        self._catalog = catalog or AttributionExperienceCatalog.load_default()

    def advise(
        self,
        *,
        parsed_intent: ParsedIntent,
        available_lanes: Sequence[DiscoveryLane],
        memory_hints: Sequence[Any] = (),
        allow_memory_priority: bool = True,
    ) -> AttributionExperienceAdvice | None:
        playbook = self._catalog.resolve(
            metric_id=parsed_intent.metric_id,
            question_family=parsed_intent.question_family,
        )
        if playbook is None:
            return None

        available_refs = _unique_lane_refs(_lane_ref(lane) for lane in available_lanes)
        available_by_key = {lane.key(): lane for lane in available_refs}
        static_priority = (
            [
                available_by_key[lane.key()]
                for lane in playbook.lane_priority
                if lane.key() in available_by_key
            ]
            if parsed_intent.analysis_strategy == "standard"
            else []
        )

        memory_priority: list[ExperienceLaneRef] = []
        source_memory_ids: list[str] = []
        memory_allowed = (
            allow_memory_priority
            and parsed_intent.analysis_strategy == "standard"
            and bool(memory_hints)
        )
        if memory_allowed:
            for hint in sorted(memory_hints, key=lambda item: float(getattr(item, "confidence", 0.0)), reverse=True):
                if getattr(hint, "metric_id", None) != parsed_intent.metric_id:
                    continue
                if float(getattr(hint, "confidence", 0.0)) < 0.70:
                    continue
                dimensions = {str(item) for item in getattr(hint, "preferred_dimensions", [])}
                signal_types = {str(item) for item in getattr(hint, "preferred_signal_types", [])}
                matched = [
                    lane
                    for lane in available_refs
                    if (not dimensions or lane.dimension in dimensions)
                    and (not signal_types or lane.signal_type in signal_types)
                ]
                if not matched:
                    continue
                memory_priority.extend(matched)
                source_memory_ids.extend(str(item) for item in getattr(hint, "source_memory_ids", []))

        execution_priority = _unique_lane_refs(
            [*memory_priority, *static_priority, *available_refs]
        )
        return AttributionExperienceAdvice(
            playbook_id=playbook.playbook_id,
            hypotheses=playbook.hypotheses,
            execution_lane_priority=execution_priority,
            required_lanes=available_refs,
            retention=playbook.retention,
            residual=playbook.residual,
            memory_mode="priority_only" if memory_priority else "disabled",
            source_memory_ids=_ordered_unique(source_memory_ids),
        )


def _lane_ref(lane: DiscoveryLane) -> ExperienceLaneRef:
    return ExperienceLaneRef(
        dimension=lane.dimension,
        signal_type=lane.signal_type,
        alias_discriminator=lane.alias_discriminator,
    )


def _unique_lane_refs(values: Sequence[ExperienceLaneRef] | Any) -> list[ExperienceLaneRef]:
    unique: list[ExperienceLaneRef] = []
    seen: set[tuple[str, str, str | None]] = set()
    for value in values:
        key = value.key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _ordered_unique(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _assert_non_answer_bearing(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _BANNED_CONFIG_KEYS:
                raise AttributionExperienceError(
                    "EXPERIENCE_ANSWER_BEARING_CONFIG",
                    f"forbidden key {path}.{key}",
                )
            _assert_non_answer_bearing(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_non_answer_bearing(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _BANNED_TEXT_PATTERNS:
            if pattern.search(value):
                raise AttributionExperienceError(
                    "EXPERIENCE_ANSWER_BEARING_CONFIG",
                    f"forbidden text at {path}",
                )


__all__ = [
    "AttributionExperienceAdvice",
    "AttributionExperienceAdvisor",
    "AttributionExperienceCatalog",
    "AttributionExperienceError",
    "AttributionHypothesis",
    "AttributionPlaybook",
    "CandidateRetentionGuide",
    "EvidenceBranch",
    "ExperienceLaneRef",
    "ResidualGapGuide",
]
