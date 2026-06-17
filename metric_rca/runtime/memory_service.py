"""Runtime memory integration for deterministic RCA planning."""

from __future__ import annotations

from typing import Any, Protocol

from metric_rca.runtime.plan_models import CasePrior
from metric_rca.services.metric_contracts import ParsedIntent


FORBIDDEN_MEMORY_PRIOR_FIELDS = frozenset(
    {
        "expected_element",
        "expected_root_cause_type",
        "expected_dimension",
        "expected_metric_id",
        "expected_anomaly",
        "expected_business_date",
        "element",
        "root_cause_type",
    }
)


class RuntimeMemoryProtocol(Protocol):
    def read_priors(self, run_id: str, parsed_intent: ParsedIntent) -> list[CasePrior]:
        ...

    def write_verified_case(self, run_id: str, report: dict[str, Any], reflection: Any, parsed_intent: ParsedIntent) -> None:
        ...

    def write_reflection_failure(
        self,
        run_id: str,
        error_code: str,
        parsed_intent: ParsedIntent,
        extra: dict[str, Any] | None = None,
    ) -> None:
        ...


class RuntimeMemoryService:
    def __init__(self, *, dependencies: Any) -> None:
        self._dependencies = dependencies

    def read_priors(self, run_id: str, parsed_intent: ParsedIntent) -> list[CasePrior]:
        if not _memory_enabled(self._dependencies.settings):
            return []
        repo = self._memory_repo()
        mem_keys = [f"{parsed_intent.metric_id}|semantic", f"{parsed_intent.metric_id}|run"]
        raw_hits: list[dict[str, Any]] = []
        try:
            raw_hits.extend(repo.read_layers(mem_keys[0], layers=("semantic",)))
            raw_hits.extend(repo.read_layers(mem_keys[1], layers=("episodic", "reflection", "case")))
            eval_suite = str(getattr(self._dependencies.settings, "eval_suite", "") or "")
            eval_scoped_hits = _filter_memory_hits_by_eval_suite(raw_hits, eval_suite=eval_suite)
            _raise_for_forbidden_memory_fields(eval_scoped_hits)
            scope = _parsed_intent_scope(parsed_intent)
            scoped_hits = _filter_memory_hits_by_scope(eval_scoped_hits, scope=scope)
            hits = _filter_memory_hits_by_intent(scoped_hits, parsed_intent=parsed_intent)
            priors = [_case_prior_from_hit(parsed_intent.metric_id, hit) for hit in hits]
            priors = [prior for prior in priors if prior is not None]
            self._write_memory_trace(
                run_id=run_id,
                input_summary={
                    "metric_id": parsed_intent.metric_id,
                    "mem_keys": mem_keys,
                    "layers": ["semantic", "episodic", "reflection", "case"],
                    "eval_suite": eval_suite,
                    "filters": scope,
                },
                output_summary={
                    "hit_count": len(hits),
                    "excluded_hit_count": len(raw_hits) - len(hits),
                    "prior_count": len(priors),
                    "hits": [_memory_hit_audit(hit) for hit in hits],
                },
                error_code=None,
            )
            return priors
        except RuntimeError as exc:
            self._write_memory_trace(
                run_id=run_id,
                input_summary={"metric_id": parsed_intent.metric_id, "mem_keys": mem_keys},
                output_summary={"error": str(exc)},
                error_code="MEMORY_READ_FAILED",
            )
            raise RuntimeError("MEMORY_READ_FAILED: memory read failed") from exc

    def write_verified_case(self, run_id: str, report: dict[str, Any], reflection: Any, parsed_intent: ParsedIntent) -> None:
        if not _memory_write_enabled(self._dependencies.settings):
            return
        repo = self._memory_repo()
        status = str(report.get("status") or "")
        candidate = report.get("top_candidate") if isinstance(report.get("top_candidate"), dict) else {}
        preferred_dimensions = _list_value(candidate.get("dimension")) if isinstance(candidate, dict) else []
        prior_root_causes = _list_value(candidate.get("root_cause_type")) if isinstance(candidate, dict) else []
        if status == "no_anomaly":
            prior_root_causes = ["no_anomaly"]
        payload = {
            "run_id": run_id,
            "metric_id": parsed_intent.metric_id,
            "question_family": parsed_intent.question_family,
            "analysis_strategy": parsed_intent.analysis_strategy,
            "preferred_dimensions": preferred_dimensions,
            "prior_root_causes": prior_root_causes,
            "verdict": candidate.get("verdict") if isinstance(candidate, dict) else status,
            "filters": _parsed_intent_scope(parsed_intent),
            "reflection_repair_count": int(getattr(reflection, "repair_count", 0) or 0),
        }
        try:
            repo.write(
                {
                    "layer": "episodic",
                    "mem_key": f"{parsed_intent.metric_id}|run",
                    "payload": payload,
                    "confidence": 0.8,
                    "source": "reflection_verified",
                }
            )
        except RuntimeError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED: memory write failed") from exc

    def write_reflection_failure(
        self,
        run_id: str,
        error_code: str,
        parsed_intent: ParsedIntent,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not _memory_write_enabled(self._dependencies.settings):
            return
        repo = self._memory_repo()
        payload = {
            "run_id": run_id,
            "metric_id": parsed_intent.metric_id,
            "error_code": error_code,
            "filters": _parsed_intent_scope(parsed_intent),
            **(extra or {}),
        }
        try:
            repo.write(
                {
                    "layer": "reflection",
                    "mem_key": f"{parsed_intent.metric_id}|run",
                    "payload": payload,
                    "confidence": 0.75,
                    "source": "reflection_verified",
                }
            )
        except RuntimeError as exc:
            raise RuntimeError("MEMORY_WRITE_FAILED: reflection memory write failed") from exc

    def _memory_repo(self) -> Any:
        repo = getattr(self._dependencies, "memory_repo", None)
        if repo is None:
            raise RuntimeError("MEMORY_READ_FAILED: memory repository unavailable")
        return repo

    def _write_memory_trace(
        self,
        *,
        run_id: str,
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        error_code: str | None,
    ) -> None:
        self._dependencies.trace_writer.write_step(
            run_id=run_id,
            node="memory_read",
            action="read_priors",
            input_summary=input_summary,
            output_summary=output_summary,
            error_code=error_code,
        )


def _case_prior_from_hit(metric_id: str, hit: dict[str, Any]) -> CasePrior | None:
    forbidden = sorted(_forbidden_memory_fields(hit))
    if forbidden:
        raise RuntimeError(f"MEMORY_READ_FAILED: answer-bearing memory fields are forbidden: {forbidden}")
    preferred_dimensions = _strings(hit.get("preferred_dimensions") or hit.get("dimension"))
    preferred_signal_types = _strings(hit.get("preferred_signal_types") or hit.get("signal_type"))
    prior_root_causes = _strings(hit.get("prior_root_causes"))
    if not preferred_dimensions and not preferred_signal_types and not prior_root_causes:
        return None
    return CasePrior(
        metric_id=metric_id,
        preferred_dimensions=preferred_dimensions,
        preferred_signal_types=preferred_signal_types,
        prior_root_causes=prior_root_causes,
        confidence=float(hit.get("confidence", 0.0)),
        source_memory_ids=_strings(hit.get("memory_id")),
    )


def _raise_for_forbidden_memory_fields(hits: list[dict[str, Any]]) -> None:
    forbidden_by_memory: dict[str, list[str]] = {}
    for hit in hits:
        forbidden = sorted(_forbidden_memory_fields(hit))
        if forbidden:
            memory_id = str(hit.get("memory_id") or "<unknown>")
            forbidden_by_memory[memory_id] = forbidden
    if forbidden_by_memory:
        raise RuntimeError(f"MEMORY_READ_FAILED: answer-bearing memory fields are forbidden: {forbidden_by_memory}")


def _forbidden_memory_fields(value: Any) -> set[str]:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_MEMORY_PRIOR_FIELDS & {str(key) for key in value}
        for child in value.values():
            forbidden |= _forbidden_memory_fields(child)
        return forbidden
    if isinstance(value, list | tuple | set):
        forbidden: set[str] = set()
        for child in value:
            forbidden |= _forbidden_memory_fields(child)
        return forbidden
    return set()


def _filter_memory_hits_by_scope(raw_hits: list[dict[str, Any]], *, scope: dict[str, str]) -> list[dict[str, Any]]:
    if not scope:
        return raw_hits
    hits: list[dict[str, Any]] = []
    for hit in raw_hits:
        filters = hit.get("filters")
        if filters is None:
            hits.append(hit)
        elif isinstance(filters, dict) and {str(key): str(value) for key, value in filters.items()} == scope:
            hits.append(hit)
    return hits


def _filter_memory_hits_by_eval_suite(raw_hits: list[dict[str, Any]], *, eval_suite: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for hit in raw_hits:
        allowed_suites = _strings(hit.get("eval_suites"))
        if not allowed_suites:
            hits.append(hit)
            continue
        if eval_suite in allowed_suites:
            hits.append(hit)
    return hits


def _filter_memory_hits_by_intent(
    raw_hits: list[dict[str, Any]],
    *,
    parsed_intent: ParsedIntent,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for hit in raw_hits:
        if str(hit.get("layer") or "") == "semantic":
            hits.append(hit)
            continue
        if hit.get("question_family") != parsed_intent.question_family:
            continue
        if hit.get("analysis_strategy") != parsed_intent.analysis_strategy:
            continue
        hits.append(hit)
    return hits


def _memory_hit_audit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": hit.get("memory_id"),
        "layer": hit.get("layer"),
        "mem_key": hit.get("mem_key"),
        "confidence": hit.get("confidence"),
        "source": hit.get("source"),
    }


def _parsed_intent_scope(parsed_intent: ParsedIntent) -> dict[str, str]:
    if len(parsed_intent.filters) == 1:
        key, value = next(iter(parsed_intent.filters.items()))
        return {str(key): str(value)}
    if parsed_intent.dimension is not None and parsed_intent.element is not None:
        return {str(parsed_intent.dimension): str(parsed_intent.element)}
    return {}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _list_value(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(value)]


def _memory_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "memory_enabled", False))


def _memory_write_enabled(settings: Any) -> bool:
    return _memory_enabled(settings) and bool(getattr(settings, "memory_write_on_finalize", True))
