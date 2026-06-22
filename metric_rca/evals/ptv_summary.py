"""Deterministic PTV optimization summary and formal two-green evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from metric_rca.evals.ptv_artifacts import read_json, read_jsonl, utc_now_iso, write_json_atomic
from metric_rca.evals.ptv_errors import PtvErrorCode, PtvRuntimeError


TRACKED_METRICS = (
    "top1_rate",
    "top3_rate",
    "root_cause_set_recall_avg",
    "root_cause_set_precision_avg",
    "weighted_explanation_coverage_avg",
)
FIX_PRIORITY = (
    "FIX-ENUM",
    "FIX-D",
    "FIX-INJ",
    "FIX-M",
    "FIX-A",
    "FIX-P",
    "FIX-T",
    "FIX-B",
    "FIX-S",
    "FIX-G",
    "STRUCTURAL",
)


@dataclass(frozen=True)
class GreenConfirmation:
    first_green_round: int | None
    confirmation_round: int | None
    confirmed: bool
    pending: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "first_green_round": self.first_green_round,
            "confirmation_round": self.confirmation_round,
            "confirmed": self.confirmed,
            "pending": self.pending,
            "reason": self.reason,
        }


def build_round_summaries(
    *,
    cycle_id: str,
    round_number: int,
    round_dir: Path,
    previous_round_dirs: Iterable[Path],
    selected_fix_category: str | None,
    selected_layer: str | None,
    controller_justification: str,
    revert_decision: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    eval_result = read_json(round_dir / "eval-result.json")
    gap_report = read_json(round_dir / "gap_report.json")
    diagnosis = read_jsonl(round_dir / "diagnosis.jsonl", allow_empty=True)
    lineage = read_json(round_dir / "commit_lineage.json")
    previous = _load_previous_rounds(previous_round_dirs)
    metrics_after = dict(eval_result.get("summary", {}))
    if not isinstance(eval_result.get("cases"), list):
        raise PtvRuntimeError(PtvErrorCode.EVAL_RESULT_INVALID, "eval-result cases must be a list")
    _validate_eval_summary_contract(metrics_after, case_count=len(eval_result["cases"]))

    metrics_before = previous[-1]["metrics_after"] if previous else {}
    regressed_metrics = _regressed_metrics(metrics_before, metrics_after)
    categories = [str(row.get("fix_category")) for row in diagnosis if row.get("fix_category") not in {None, "", "NO-FIX"}]
    category_counts = Counter(categories)
    selected = selected_fix_category or _recommended_category(category_counts)
    if selected is None and not _gates_passed(metrics_after):
        raise PtvRuntimeError(
            PtvErrorCode.ANALYST_OUTPUT_INVALID,
            "a non-green round requires a selected fix category",
        )
    if not controller_justification.strip():
        raise PtvRuntimeError(PtvErrorCode.ANALYST_OUTPUT_INVALID, "controller justification must not be empty")

    rules = _controller_rules(
        previous=previous,
        current_category_counts=category_counts,
        selected_fix_category=selected,
        regressed_metrics=regressed_metrics,
        revert_decision=revert_decision,
    )
    remaining_gaps = _remaining_gaps(diagnosis)
    failure_patterns = _failure_patterns(diagnosis)
    current_fingerprint = eval_contract_fingerprint(eval_result)
    confirmation = formal_two_green_status(
        round_number=round_number,
        current_metrics=metrics_after,
        current_lineage=lineage,
        current_eval_contract_fingerprint=current_fingerprint,
        previous=previous,
    )
    memory_treatment = _memory_treatment_status(metrics_after)
    optimization_summary = {
        "schema_version": "metricrca-ptv-optimization-summary-v2",
        "summary_type": "agent_optimization_context",
        "cycle_id": cycle_id,
        "round": round_number,
        "eval_id": eval_result.get("eval_id"),
        "generated_at": utc_now_iso(),
        "eval_code_commit": lineage["eval_code_commit"],
        "fix_commit": lineage.get("fix_commit"),
        "post_eval_review_fix_commit": lineage.get("post_eval_review_fix_commit"),
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "gap_summary": gap_report.get("summary", {}),
        "failure_patterns": failure_patterns,
        "remaining_gaps": remaining_gaps,
        "selected_fix_category": selected,
        "selected_layer": selected_layer,
        "controller_justification": controller_justification.strip(),
        "controller_rules_applied": rules,
        "formal_two_green": confirmation.as_dict(),
        "memory_treatment": memory_treatment,
        "eval_contract_fingerprint": current_fingerprint,
    }
    write_json_atomic(round_dir / "optimization_summary.json", optimization_summary)

    summary = {
        "schema_version": "metricrca-ptv-round-summary-v2",
        "cycle_id": cycle_id,
        "round": round_number,
        "eval_id": eval_result.get("eval_id"),
        "status": "green" if _gates_passed(metrics_after) else "red",
        "metricrca_gates_passed": _gates_passed(metrics_after),
        "thresholds_met": bool(metrics_after.get("thresholds_met")),
        "case_total": metrics_after.get("case_total"),
        "completed_case_total": metrics_after["completed_case_total"],
        "metrics_after": metrics_after,
        "eval_code_commit": lineage["eval_code_commit"],
        "fix_commit": lineage.get("fix_commit"),
        "post_eval_review_fix_commit": lineage.get("post_eval_review_fix_commit"),
        "formal_two_green_confirmation_pending": confirmation.pending,
        "formal_two_green_confirmed": confirmation.confirmed,
        "formal_two_green": confirmation.as_dict(),
        "memory_treatment": memory_treatment,
        "top1_residual_cases": [
            str(row.get("case_id"))
            for row in eval_result["cases"]
            if int(row.get("top1_ok", row.get("dominant_top1_ok", 0)) or 0) == 0
        ],
        "top3_residual_cases": [
            str(row.get("case_id"))
            for row in eval_result["cases"]
            if int(row.get("top3_ok", row.get("top3_contains_all_major_causes", 0)) or 0) == 0
        ],
    }
    write_json_atomic(round_dir / "summary.json", summary)
    return optimization_summary, summary


def formal_two_green_status(
    *,
    round_number: int,
    current_metrics: Mapping[str, Any],
    current_lineage: Mapping[str, Any],
    current_eval_contract_fingerprint: str,
    previous: list[dict[str, Any]],
) -> GreenConfirmation:
    if not _gates_passed(current_metrics):
        return GreenConfirmation(None, None, confirmed=False, pending=False, reason="current round is not green")
    if current_lineage.get("post_eval_review_fix_commit"):
        return GreenConfirmation(
            round_number,
            None,
            confirmed=False,
            pending=True,
            reason="code changed after eval; the post-review fix requires a new first green run",
        )
    if not previous:
        return GreenConfirmation(round_number, None, confirmed=False, pending=True, reason="first green round requires confirmation")
    prior = previous[-1]
    prior_metrics = prior["metrics_after"]
    prior_lineage = prior["lineage"]
    if not _gates_passed(prior_metrics):
        return GreenConfirmation(round_number, None, confirmed=False, pending=True, reason="previous round was not green")
    if prior_lineage.get("post_eval_review_fix_commit"):
        return GreenConfirmation(round_number, None, confirmed=False, pending=True, reason="previous green was invalidated by a post-eval code fix")
    if prior_lineage.get("eval_code_commit") != current_lineage.get("eval_code_commit"):
        return GreenConfirmation(round_number, None, confirmed=False, pending=True, reason="green rounds evaluated different code commits")
    if prior.get("eval_contract_fingerprint") != current_eval_contract_fingerprint:
        return GreenConfirmation(round_number, None, confirmed=False, pending=True, reason="eval contract changed between green runs")
    return GreenConfirmation(
        int(prior["round"]),
        round_number,
        confirmed=True,
        pending=False,
        reason="two consecutive rounds evaluated the same commit under the same contract and passed gates",
    )


def eval_contract_fingerprint(eval_result: Mapping[str, Any]) -> str:
    summary = eval_result.get("summary", {})
    cases = eval_result.get("cases", [])
    payload = {
        "eval_suite": summary.get("eval_suite"),
        "case_ids": sorted(str(row.get("case_id")) for row in cases if isinstance(row, Mapping)),
        "case_total": summary.get("case_total"),
        "tracked_metric_names": list(TRACKED_METRICS),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _load_previous_rounds(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(paths):
        summary_path = path / "summary.json"
        optimization_path = path / "optimization_summary.json"
        lineage_path = path / "commit_lineage.json"
        eval_path = path / "eval-result.json"
        required = (summary_path, optimization_path, lineage_path, eval_path)
        missing = [item.name for item in required if not item.exists()]
        if missing:
            raise PtvRuntimeError(
                PtvErrorCode.ARTIFACT_MISSING,
                "previous PTV round is missing required history artifacts",
                context={"round_dir": str(path), "missing": missing},
            )
        summary = read_json(summary_path)
        optimization = read_json(optimization_path)
        lineage = read_json(lineage_path)
        eval_result = read_json(eval_path)
        records.append(
            {
                "round": int(summary["round"]),
                "metrics_after": dict(summary.get("metrics_after", {})),
                "selected_fix_category": optimization.get("selected_fix_category"),
                "remaining_gaps": optimization.get("remaining_gaps", []),
                "lineage": lineage,
                "eval_contract_fingerprint": optimization.get("eval_contract_fingerprint") or eval_contract_fingerprint(eval_result),
            }
        )
    return records


def _controller_rules(
    *,
    previous: list[dict[str, Any]],
    current_category_counts: Counter[str],
    selected_fix_category: str | None,
    regressed_metrics: list[str],
    revert_decision: str | None,
) -> dict[str, Any]:
    previous_category = previous[-1].get("selected_fix_category") if previous else None
    blocked = [previous_category] if previous_category and regressed_metrics else []
    deferred_counts: Counter[str] = Counter()
    for row in previous[-2:]:
        round_categories = {
            str(gap["category"])
            for gap in row.get("remaining_gaps", [])
            if isinstance(gap, Mapping) and isinstance(gap.get("category"), str)
        }
        deferred_counts.update(round_categories)
    promoted = next(
        (category for category in FIX_PRIORITY if deferred_counts.get(category, 0) >= 2),
        None,
    )
    discovery_priority = current_category_counts.get("FIX-D", 0) > 0 and current_category_counts.get("FIX-A", 0) > 0
    streak_counts = _category_streak_counts(previous, selected_fix_category)
    if selected_fix_category in blocked:
        raise PtvRuntimeError(
            PtvErrorCode.ANALYST_OUTPUT_INVALID,
            "selected fix category violates RULE-C1 regression block",
            context={"selected": selected_fix_category, "blocked": blocked},
        )
    if promoted is not None and selected_fix_category not in {promoted, None}:
        raise PtvRuntimeError(
            PtvErrorCode.ANALYST_OUTPUT_INVALID,
            "selected fix category violates RULE-C2 mandatory promotion",
            context={"selected": selected_fix_category, "promoted": promoted},
        )
    if discovery_priority and selected_fix_category == "FIX-A":
        raise PtvRuntimeError(
            PtvErrorCode.ANALYST_OUTPUT_INVALID,
            "selected fix category violates RULE-C3 discovery-before-attribution",
        )
    if selected_fix_category and streak_counts.get(selected_fix_category, 0) > 2:
        raise PtvRuntimeError(
            PtvErrorCode.FIX_CATEGORY_STALL,
            "selected fix category violates RULE-C5 consecutive limit",
            context={"selected": selected_fix_category, "streak": streak_counts[selected_fix_category]},
        )
    if len(regressed_metrics) >= 2 and revert_decision not in {"revert", "keep"}:
        raise PtvRuntimeError(
            PtvErrorCode.ANALYST_OUTPUT_INVALID,
            "RULE-C4 requires revert_decision=revert|keep when at least two aggregate metrics regress",
            context={"regressed_metrics": regressed_metrics},
        )
    return {
        "rule_c1_blocked_categories": blocked,
        "rule_c2_promoted": promoted,
        "rule_c3_discovery_priority": discovery_priority,
        "rule_c4_revert_assessment": {
            "triggered": len(regressed_metrics) >= 2,
            "regressed_metrics": regressed_metrics,
            "revert_decision": revert_decision if len(regressed_metrics) >= 2 else "keep",
        },
        "rule_c5_streak_counts": streak_counts,
    }


def _regressed_metrics(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    regressed: list[str] = []
    for name in TRACKED_METRICS:
        old = before.get(name)
        new = after.get(name)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and float(new) < float(old) - 1e-12:
            regressed.append(name)
    return regressed


def _category_streak_counts(previous: list[dict[str, Any]], current: str | None) -> dict[str, int]:
    sequence = [str(row["selected_fix_category"]) for row in previous if row.get("selected_fix_category")]
    if current:
        sequence.append(current)
    if not sequence:
        return {}
    last = sequence[-1]
    streak = 0
    for category in reversed(sequence):
        if category != last:
            break
        streak += 1
    return {last: streak}


def _recommended_category(counts: Counter[str]) -> str | None:
    if not counts:
        return None
    for category in FIX_PRIORITY:
        if counts.get(category, 0) > 0:
            return category
    return counts.most_common(1)[0][0]


def _remaining_gaps(diagnosis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in diagnosis:
        if row.get("divergence") == "correct" or row.get("fix_category") in {None, "", "NO-FIX"}:
            continue
        result.append(
            {
                "case_id": row.get("case_id"),
                "aspect": row.get("aspect"),
                "category": row.get("fix_category"),
                "gate_blocking": bool(row.get("gate_blocking", False)),
                "summary": row.get("root_cause_analysis") or row.get("diagnosis"),
                "proposed_files": (row.get("proposed_fix") or {}).get("files", []),
            }
        )
    return result


def _failure_patterns(diagnosis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in diagnosis:
        key = (str(row.get("fix_category", "NO-FIX")), str(row.get("diagnosis", row.get("divergence", "unknown"))))
        grouped[key].append(str(row.get("case_id")))
    return [
        {
            "fix_category": key[0],
            "pattern": key[1],
            "count": len(case_ids),
            "affected_cases": sorted(set(case_ids)),
        }
        for key, case_ids in sorted(grouped.items())
    ]


def _memory_treatment_status(metrics: Mapping[str, Any]) -> dict[str, Any]:
    suite = metrics.get("eval_suite")
    gate_value = metrics.get("memory_treatment_gate")
    if suite == "memory-treatment":
        return {
            "suite": suite,
            "gate_evaluated": True,
            "gate_passed": gate_value is True,
            "classification": "behavior_failure" if gate_value is False else "passed",
            "reason": "memory-treatment suite directly evaluates enabled/disabled causal behavior",
        }
    return {
        "suite": suite,
        "gate_evaluated": False,
        "gate_passed": None,
        "observed_field": gate_value,
        "classification": "gate_not_applicable",
        "reason": "regression suite memory rates are diagnostic; run the memory-treatment suite before classifying behavior",
    }


def _gates_passed(metrics: Mapping[str, Any]) -> bool:
    return metrics.get("thresholds_met") is True and metrics.get("per_family_gate") is True and metrics.get("complete") is True


def _validate_eval_summary_contract(metrics: Mapping[str, Any], *, case_count: int) -> None:
    required_bool = ("thresholds_met", "per_family_gate", "complete")
    missing = [name for name in ("case_total", "completed_case_total", *required_bool) if name not in metrics]
    if missing:
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "eval summary is missing required gate fields",
            context={"missing": missing},
        )
    for name in required_bool:
        if not isinstance(metrics.get(name), bool):
            raise PtvRuntimeError(
                PtvErrorCode.EVAL_RESULT_INVALID,
                "eval summary gate fields must be boolean",
                context={"field": name, "value": metrics.get(name)},
            )
    if metrics.get("case_total") != case_count or metrics.get("completed_case_total") != case_count:
        raise PtvRuntimeError(
            PtvErrorCode.EVAL_RESULT_INVALID,
            "eval summary case counts must match persisted cases",
            context={
                "case_total": metrics.get("case_total"),
                "completed_case_total": metrics.get("completed_case_total"),
                "actual_cases": case_count,
            },
        )
