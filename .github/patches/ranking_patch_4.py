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

replace_once('metric_rca/runtime/ranking.py', 'def _has_any_pair_matching_signal_evidence(\n', 'def _has_verified_interaction_mechanism_evidence(\n    *,\n    repository: Any,\n    run_id: str,\n    candidate: RootCauseCandidate,\n    required_bad_direction: bool,\n) -> bool:\n    pairs = _candidate_pairs(candidate)\n    if not _has_dimension(pairs, "channel") or not _has_dimension(pairs, "category"):\n        return False\n    for dimension in ("channel", "category"):\n        dimension_pairs = [\n            (pair_dimension, element)\n            for pair_dimension, element in pairs\n            if pair_dimension == dimension\n        ]\n        if not any(\n            _has_matching_signal_for_pair(\n                repository=repository,\n                run_id=run_id,\n                dimension=pair_dimension,\n                element=element,\n                required_bad_direction=required_bad_direction,\n                required_signal_type="interaction",\n            )\n            for pair_dimension, element in dimension_pairs\n        ):\n            return False\n    return True\n\n\ndef _signal_type_for_candidate(candidate: RootCauseCandidate) -> str | None:\n    by_root_cause = {\n        RootCauseType.CAMPAIGN_TRAFFIC_DROP.value: "campaign",\n        RootCauseType.CONVERSION_DROP.value: "conversion",\n        RootCauseType.STOCKOUT.value: "inventory",\n        RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value: "refund_quality",\n        RootCauseType.INTERACTION_CHANNEL_CATEGORY.value: "interaction",\n    }\n    return by_root_cause.get(candidate.root_cause_type)\n\n\ndef _has_any_pair_matching_signal_evidence(\n')

replace_once('metric_rca/runtime/ranking.py', '        if alias in {f"E_select_{dimension}" for dimension in dimensions}:\n            evidence_ids.append(evidence_id)\n            continue\n        if alias in {f"E4_{dimension}" for dimension in dimensions}:\n            evidence_ids.append(evidence_id)\n            continue\n', '        if any(alias == f"E_select_{dimension}" or alias.startswith(f"E_select_{dimension}_") for dimension in dimensions):\n            evidence_ids.append(evidence_id)\n            continue\n        if any(alias == f"E4_{dimension}" or alias.startswith(f"E4_{dimension}_") for dimension in dimensions):\n            evidence_ids.append(evidence_id)\n            continue\n')

replace_once('metric_rca/runtime/ranking.py', '        if alias == "E3" or alias.startswith("E3_"):\n            if not isinstance(summary, dict):\n                continue\n            pair = (str(summary.get("dimension")), str(summary.get("element")))\n', '        if alias == "E3" or alias.startswith("E3_"):\n            if not isinstance(summary, dict) or summary.get("signal_type") != "interaction":\n                continue\n            pair = (str(summary.get("dimension")), str(summary.get("element")))\n')
