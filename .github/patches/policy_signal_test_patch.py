from __future__ import annotations
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "tests/test_interaction_evidence_ranking.py"
source = path.read_text(encoding="utf-8")
old = (
    "    assert _signal_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=\"run\",\n"
    "        persisted_selected_candidate=interaction,\n"
)
new = (
    "    assert _signal_verified_ranked_candidate(\n"
    "        repository=repository,\n"
    "        run_id=\"run\",\n"
    "        metric_id=\"gmv\",\n"
    "        persisted_selected_candidate=interaction,\n"
)
count = source.count(old)
if count != 1:
    raise RuntimeError(f"expected one test signature replacement, found {count}")
path.write_text(source.replace(old, new, 1), encoding="utf-8")
