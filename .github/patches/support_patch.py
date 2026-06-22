from __future__ import annotations
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one replacement, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")

def append_once(relative: str, marker: str, addition: str) -> None:
    path = ROOT / relative
    source = path.read_text(encoding="utf-8")
    if marker in source:
        raise RuntimeError(f"{relative}: marker already present")
    path.write_text(source.rstrip() + "\n" + addition.lstrip(), encoding="utf-8")

replace_once('metric_rca/runtime/plan_models.py', 'from metric_rca.domain.models import StrictModel\n', 'from metric_rca.business.attribution_experience import AttributionExperienceAdvice\nfrom metric_rca.domain.models import StrictModel\n')

replace_once('metric_rca/runtime/plan_models.py', '    memory_hints: list[CasePrior] = Field(default_factory=list)\n', '    memory_hints: list[CasePrior] = Field(default_factory=list)\n    experience_advice: AttributionExperienceAdvice | None = None\n')

replace_once('pyproject.toml', '[tool.setuptools.packages.find]\ninclude = ["metric_rca*"]\n\n', '[tool.setuptools.packages.find]\ninclude = ["metric_rca*"]\n\n[tool.setuptools.package-data]\n"metric_rca.business" = ["attribution_playbooks.yaml"]\n\n')

replace_once('metric_rca/agent/tools/schemas.py', 'from pydantic import Field\n\nfrom metric_rca.domain.models import Evidence, Observation, RootCauseCandidate, StrictModel\n', 'from pydantic import Field\n\nfrom metric_rca.business.attribution_experience import AttributionExperienceAdvice\nfrom metric_rca.domain.models import Evidence, Observation, RootCauseCandidate, StrictModel\n')

replace_once('metric_rca/agent/tools/schemas.py', 'class MergeContributionSetsArgs(StrictModel):\n    run_id: str\n    metric_id: str\n    target_date: date\n    source_evidence_aliases: list[str]\n', 'class MergeContributionSetsArgs(StrictModel):\n    run_id: str\n    metric_id: str\n    target_date: date\n    source_evidence_aliases: list[str]\n    experience_advice: AttributionExperienceAdvice | None = None\n')

replace_once('metric_rca/agent/tools/merge_contribution_sets.py', '        "candidate_composition_strategy": COMPOSITION_STRATEGY,\n    }\n    decomposition = selected_source_summary.get("decomposition")\n', '        "candidate_composition_strategy": COMPOSITION_STRATEGY,\n    }\n    if args.experience_advice is not None:\n        result_summary["experience_advice"] = args.experience_advice.model_dump(mode="json")\n    decomposition = selected_source_summary.get("decomposition")\n')

append_once('tests/test_runtime_plan.py', 'test_memory_priority_preserves_full_lane_coverage_and_canonical_merge_order', '\n\ndef test_memory_priority_preserves_full_lane_coverage_and_canonical_merge_order() -> None:\n    parsed = ParsedIntent(\n        metric_id="gmv",\n        target_date=date(2026, 6, 5),\n        question_family="gmv_drop",\n        analysis_strategy="standard",\n    )\n    prior = CasePrior(\n        metric_id="gmv",\n        preferred_dimensions=["product"],\n        preferred_signal_types=["inventory"],\n        prior_root_causes=["stockout"],\n        confidence=0.95,\n        source_memory_ids=["memory-product"],\n    )\n\n    baseline = _compiler().compile(run_id="run-baseline", parsed_intent=parsed)\n    with_memory = _compiler().compile(\n        run_id="run-memory",\n        parsed_intent=parsed,\n        memory_hints=[prior],\n    )\n\n    baseline_fetches = [\n        (\n            action.args["dimension"],\n            action.args["signal_type"],\n            action.args.get("evidence_alias"),\n        )\n        for action in baseline.actions\n        if action.kind == "fetch_related_signal"\n    ]\n    memory_fetches = [\n        (\n            action.args["dimension"],\n            action.args["signal_type"],\n            action.args.get("evidence_alias"),\n        )\n        for action in with_memory.actions\n        if action.kind == "fetch_related_signal"\n    ]\n    baseline_merge = next(action for action in baseline.actions if action.kind == "merge_contribution_sets")\n    memory_merge = next(action for action in with_memory.actions if action.kind == "merge_contribution_sets")\n\n    assert len(baseline_fetches) == len(memory_fetches) == 6\n    assert set(baseline_fetches) == set(memory_fetches)\n    assert memory_fetches[0][:2] == ("product", "inventory")\n    assert (\n        memory_merge.args["source_evidence_aliases"]\n        == baseline_merge.args["source_evidence_aliases"]\n    )\n    assert with_memory.experience_advice is not None\n    assert with_memory.experience_advice.memory_mode == "priority_only"\n    assert with_memory.experience_advice.source_memory_ids == ["memory-product"]\n    assert len(with_memory.experience_advice.required_lanes) == 6\n    assert memory_merge.args["experience_advice"]["memory_mode"] == "priority_only"\n')

replace_once('tests/test_project_contract.py', '    assert declared["openai-agents"] == "openai-agents==0.17.5"\n\n    installed = metadata("metric_rca")\n', '    assert declared["openai-agents"] == "openai-agents==0.17.5"\n    assert pyproject["tool"]["setuptools"]["package-data"]["metric_rca.business"] == [\n        "attribution_playbooks.yaml"\n    ]\n\n    installed = metadata("metric_rca")\n')
