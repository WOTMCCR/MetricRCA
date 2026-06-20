"""Write mechanical PTV diagnosis rows from analyst_input.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _category(row: dict[str, Any]) -> str:
    if row.get("divergence") == "overfit":
        return "NO-FIX"
    aspect = str(row.get("aspect", ""))
    detail = str(row.get("detail", "")).lower()
    if aspect == "intent":
        return "FIX-P"
    if aspect == "execution":
        return "FIX-T"
    if aspect == "evidence":
        return "FIX-G"
    if aspect == "memory":
        return "FIX-T"
    if aspect == "multi_cause_outcome" or "candidate" in detail or "top3" in detail:
        return "FIX-D"
    if aspect == "outcome":
        return "FIX-D" if row.get("divergence") == "complexity_gap" else "FIX-A"
    return "STRUCTURAL"


def _layer(category: str) -> str:
    return {
        "NO-FIX": "prediction-calibration",
        "FIX-P": "intent/planning",
        "FIX-T": "tool-runtime/evidence-contract",
        "FIX-G": "reflection/evidence-graph",
        "FIX-D": "discovery/plan-candidate-generation",
        "FIX-A": "attribution/ranking",
    }.get(category, "cross-layer")


def main() -> int:
    analyst_input_path = Path(os.environ["METRIC_RCA_PTV_ANALYST_INPUT"])
    output_path = Path(os.environ["METRIC_RCA_PTV_DIAGNOSIS_PATH"])
    payload = json.loads(analyst_input_path.read_text(encoding="utf-8"))
    rows = []
    for row in payload.get("divergent_gaps", []):
        if not isinstance(row, dict):
            continue
        category = _category(row)
        rows.append(
            {
                "case_id": row.get("case_id"),
                "aspect": row.get("aspect"),
                "divergence": row.get("divergence"),
                "fix_category": category,
                "layer": _layer(category),
                "diagnosis": (
                    "metric_rca/evals/gap_analyzer.py classified prediction versus persisted eval output; "
                    f"detail={row.get('detail')}"
                ),
                "recommended_action": (
                    "No code fix; recalibrate prediction." if category == "NO-FIX"
                    else "Inspect the named layer before changing scorer or private eval data."
                ),
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
