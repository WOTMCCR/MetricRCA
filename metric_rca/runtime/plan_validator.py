"""Static validation for deterministic RCA plans.

The compiler owns plan construction; this module owns cross-action invariants.
Keeping these checks out of the compiler prevents validation semantics from
being spread across scenario-specific branches.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from metric_rca.runtime.evidence_identity import (
    EvidenceIdentityError,
    alias_matches,
    compose_evidence_id,
    validate_evidence_alias,
)
from metric_rca.runtime.plan_models import RcaAction


class PlanValidationError(ValueError):
    """Typed error raised when a compiled plan violates a runtime invariant."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_plan_actions(
    *,
    run_id: str,
    actions: Sequence[RcaAction],
    initial_aliases: Iterable[str] = (),
) -> None:
    """Validate aliases, dependencies, producers, and action contracts.

    ``initial_aliases`` supports resumable execution, where persisted evidence
    exists before the newly compiled action suffix. Normal fresh runs pass an
    empty iterable.
    """

    available: list[str] = []
    producers: dict[str, str] = {}

    for alias in initial_aliases:
        valid_alias = _validate_identity(run_id, alias)
        if valid_alias not in available:
            available.append(valid_alias)

    for action in actions:
        _validate_dependencies(action=action, available=available)
        produced = [_validate_identity(run_id, alias) for alias in action.produces]
        _validate_action_contract(action=action, produced=produced)

        for alias in produced:
            previous = producers.get(alias)
            if previous is not None:
                raise PlanValidationError(
                    "PLAN_ALIAS_CONFLICT",
                    f"evidence alias {alias} is produced by both {previous} and {action.action_id}",
                )
            producers[alias] = action.action_id
            available.append(alias)


def _validate_identity(run_id: str, alias: str) -> str:
    try:
        valid_alias = validate_evidence_alias(alias)
        compose_evidence_id(run_id, valid_alias)
    except EvidenceIdentityError as exc:
        raise PlanValidationError(exc.code, str(exc)) from exc
    return valid_alias


def _validate_dependencies(*, action: RcaAction, available: list[str]) -> None:
    missing = [
        required
        for required in action.requires
        if not any(alias_matches(actual, required) for actual in available)
    ]
    if missing:
        raise PlanValidationError(
            "PLAN_DEPENDENCY_INVALID",
            f"action {action.action_id} requires unavailable aliases: {missing}",
        )


def _validate_action_contract(*, action: RcaAction, produced: list[str]) -> None:
    configured_alias = action.args.get("evidence_alias")
    if configured_alias is not None:
        try:
            valid_configured_alias = validate_evidence_alias(str(configured_alias))
        except EvidenceIdentityError as exc:
            raise PlanValidationError(exc.code, str(exc)) from exc
        if produced != [valid_configured_alias]:
            raise PlanValidationError(
                "PLAN_ACTION_CONTRACT_INVALID",
                (
                    f"action {action.action_id} declares evidence_alias={valid_configured_alias} "
                    f"but produces={produced}"
                ),
            )

    if action.kind == "merge_contribution_sets":
        sources = [str(alias) for alias in action.args.get("source_evidence_aliases", [])]
        if sources != action.requires or produced != ["E4"]:
            raise PlanValidationError(
                "PLAN_ACTION_CONTRACT_INVALID",
                (
                    f"merge action {action.action_id} must require its source aliases "
                    "in order and produce canonical E4"
                ),
            )

    if action.kind == "rank_root_causes" and produced != ["E_rank"]:
        raise PlanValidationError(
            "PLAN_ACTION_CONTRACT_INVALID",
            f"rank action {action.action_id} must produce canonical E_rank",
        )
