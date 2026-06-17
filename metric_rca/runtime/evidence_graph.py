"""Current-run evidence graph for deterministic plan execution."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from metric_rca.domain.models import StrictModel


class EvidenceGraph(StrictModel):
    run_id: str
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _initial_evidence_must_match_run(self) -> EvidenceGraph:
        bad_ids = [
            evidence_id
            for evidence_id in self.evidence_ids
            if not evidence_id.startswith(f"{self.run_id}:")
        ]
        if bad_ids:
            raise ValueError("EVIDENCE_SCOPE_INVALID")
        return self

    def add_ids(self, evidence_ids: list[str]) -> None:
        for evidence_id in evidence_ids:
            if not evidence_id.startswith(f"{self.run_id}:"):
                raise ValueError("EVIDENCE_SCOPE_INVALID")
            if evidence_id not in self.evidence_ids:
                self.evidence_ids.append(evidence_id)

    def has_alias(self, alias: str) -> bool:
        prefix = f"{self.run_id}:{alias}"
        return any(evidence_id == prefix or evidence_id.startswith(f"{prefix}_") for evidence_id in self.evidence_ids)

    def aliases(self) -> set[str]:
        aliases: set[str] = set()
        prefix = f"{self.run_id}:"
        for evidence_id in self.evidence_ids:
            if not evidence_id.startswith(prefix):
                continue
            local = evidence_id.removeprefix(prefix)
            aliases.add(local)
            if "_" in local:
                aliases.add(local.split("_", 1)[0])
        return aliases

    def matching(self, alias: str) -> list[str]:
        prefix = f"{self.run_id}:{alias}"
        return [
            evidence_id
            for evidence_id in self.evidence_ids
            if evidence_id == prefix or evidence_id.startswith(f"{prefix}_")
        ]

    @classmethod
    def from_repository(cls, *, run_id: str, repository: Any) -> EvidenceGraph:
        graph = cls(run_id=run_id)
        if hasattr(repository, "get_evidences"):
            graph.add_ids([row["evidence_id"] for row in repository.get_evidences(run_id)])
        return graph
