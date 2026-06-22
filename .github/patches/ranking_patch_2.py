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

replace_once('metric_rca/runtime/ranking.py', '        element=str(candidate.element),\n        required_bad_direction=required_bad_direction,\n    )\n\n\ndef _has_matching_signal_for_pair(\n', '        element=str(candidate.element),\n        required_bad_direction=required_bad_direction,\n        required_signal_type=required_signal_type,\n        excluded_signal_type=excluded_signal_type,\n    )\n\n\ndef _has_matching_signal_for_pair(\n')

replace_once('metric_rca/runtime/ranking.py', 'def _has_matching_signal_for_pair(\n    *,\n    repository: Any,\n    run_id: str,\n    dimension: str,\n    element: str,\n    required_bad_direction: bool | None = None,\n) -> bool:\n', 'def _has_matching_signal_for_pair(\n    *,\n    repository: Any,\n    run_id: str,\n    dimension: str,\n    element: str,\n    required_bad_direction: bool | None = None,\n    required_signal_type: str | None = None,\n    excluded_signal_type: str | None = None,\n) -> bool:\n')

replace_once('metric_rca/runtime/ranking.py', '        if not isinstance(summary, dict):\n            continue\n        if summary.get("dimension") != dimension or str(summary.get("element")) != element:\n', '        if not isinstance(summary, dict):\n            continue\n        signal_type = summary.get("signal_type")\n        if required_signal_type is not None and signal_type != required_signal_type:\n            continue\n        if excluded_signal_type is not None and signal_type == excluded_signal_type:\n            continue\n        if summary.get("dimension") != dimension or str(summary.get("element")) != element:\n')
