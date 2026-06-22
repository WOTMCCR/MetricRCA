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

replace_once(
    'metric_rca/runtime/ranking.py',
    '        required_bad_direction=_target_bad_direction(repository=repository, run_id=run_id),\n        required_signal_type=_signal_type_for_candidate(persisted_selected_candidate),\n',
    '        required_bad_direction=_target_bad_direction(repository=repository, run_id=run_id),\n        excluded_signal_type="interaction",\n',
)

replace_once(
    'metric_rca/runtime/ranking.py',
    'def _signal_type_for_candidate(candidate: RootCauseCandidate) -> str | None:\n    by_root_cause = {\n        RootCauseType.CAMPAIGN_TRAFFIC_DROP.value: "campaign",\n        RootCauseType.CONVERSION_DROP.value: "conversion",\n        RootCauseType.STOCKOUT.value: "inventory",\n        RootCauseType.COMPLAINT_OR_QUALITY_ISSUE.value: "refund_quality",\n        RootCauseType.INTERACTION_CHANNEL_CATEGORY.value: "interaction",\n    }\n    return by_root_cause.get(candidate.root_cause_type)\n\n\n',
    '',
)
