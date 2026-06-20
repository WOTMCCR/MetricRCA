"""Current-run evidence graph for deterministic plan execution."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from metric_rca.domain.models import StrictModel
from metric_rca.runtime.evidence_identity import (
    EvidenceIdentityError,
    alias_matches,
    evidence_belongs_to_run,
    split_evidence_id,
)


class EvidenceGraph(StrictModel):
    run_id: str
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _initial_evidence_must_match_run(self) -> EvidenceGraph:
        if any(not evidence_belongs_to_run(evidence_id, self.run_id) for evidence_id in self.evidence_ids):
            raise ValueError("EVIDENCE_SCOPE_INVALID")
        return self

    def add_ids(self, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            if not evidence_belongs_to_run(evidence_id, self.run_id):
                raise ValueError("EVIDENCE_SCOPE_INVALID")
            if evidence_id not in self.evidence_ids:
                self.evidence_ids.append(evidence_id)

    def has_alias(self, alias: str) -> bool:
        return any(alias_matches(actual, alias) for actual in self.aliases())

    def aliases(self) -> set[str]:
        aliases: set[str] = set()
        for evidence_id in self.evidence_ids:
            try:
                identity = split_evidence_id(evidence_id)
            except EvidenceIdentityError:
                continue
            if identity.run_id == self.run_id:
                aliases.add(identity.alias)
                if "_" in identity.alias:
                    aliases.add(identity.alias.split("_", 1)[0])
        return aliases

    def matching(self, alias: str) -> list[str]:
        matching_ids: list[str] = []
        for evidence_id in self.evidence_ids:
            try:
                identity = split_evidence_id(evidence_id)
            except EvidenceIdentityError:
                continue
            if identity.run_id == self.run_id and alias_matches(identity.alias, alias):
                matching_ids.append(evidence_id)
        return matching_ids

    @classmethod
    def from_repository(cls, *, run_id: str, repository: Any) -> EvidenceGraph:
        graph = cls(run_id=run_id)
        graph.add_ids([str(row["evidence_id"]) for row in repository.get_evidences(run_id)])
        return graph
