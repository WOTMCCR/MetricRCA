from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "metric_rca/runtime/ranking.py"


def replace_once(old: str, new: str) -> None:
    source = PATH.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"ranking.py: expected one replacement, found {count}")
    PATH.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "        if alias in {f\"E_select_{dimension}\" for dimension in dimensions}:\n"
    "            evidence_ids.append(evidence_id)\n"
    "            continue\n"
    "        if alias in {f\"E4_{dimension}\" for dimension in dimensions}:\n"
    "            evidence_ids.append(evidence_id)\n"
    "            continue\n",
    "        if any(\n"
    "            alias == f\"E_select_{dimension}\" or alias.startswith(f\"E_select_{dimension}_\")\n"
    "            for dimension in dimensions\n"
    "        ):\n"
    "            evidence_ids.append(evidence_id)\n"
    "            continue\n"
    "        if any(\n"
    "            alias == f\"E4_{dimension}\" or alias.startswith(f\"E4_{dimension}_\")\n"
    "            for dimension in dimensions\n"
    "        ):\n"
    "            evidence_ids.append(evidence_id)\n"
    "            continue\n",
)

replace_once(
    "        if alias == \"E3\" or alias.startswith(\"E3_\"):\n"
    "            if not isinstance(summary, dict):\n"
    "                continue\n"
    "            pair = (str(summary.get(\"dimension\")), str(summary.get(\"element\")))\n",
    "        if alias == \"E3\" or alias.startswith(\"E3_\"):\n"
    "            if not isinstance(summary, dict) or summary.get(\"signal_type\") != \"interaction\":\n"
    "                continue\n"
    "            pair = (str(summary.get(\"dimension\")), str(summary.get(\"element\")))\n",
)
