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
    "metric_rca/business/attribution_playbooks.yaml",
    "  - playbook_id: uv-interaction-v1\n"
    "    metric_ids: [uv]\n"
    "    question_families: [interaction_uv_anomaly]\n"
    "    lane_priority:\n"
    "      - dimension: channel\n"
    "        signal_type: interaction\n"
    "      - dimension: category\n"
    "        signal_type: interaction\n"
    "      - dimension: channel\n"
    "        signal_type: campaign\n"
    "        alias_discriminator: campaign\n",
    "  - playbook_id: uv-interaction-v1\n"
    "    metric_ids: [uv]\n"
    "    question_families: [interaction_uv_anomaly]\n"
    "    lane_priority:\n"
    "      - dimension: channel\n"
    "        signal_type: interaction\n"
    "      - dimension: channel\n"
    "        signal_type: campaign\n"
    "        alias_discriminator: campaign\n"
    "      - dimension: category\n"
    "        signal_type: interaction\n",
)

replace_once(
    "docs/COMPLIANCE_MATRIX.md",
    "| 3 | Plan compiler | `runtime/plan_compiler.py`, `business/discovery_policy.py`, `business/signal_policy.py` | `ParsedIntent` compiles to typed `RcaPlan`; explicit slices keep `E1/E2/E3/E4/E_rank`, while broad discovery inserts first-class `select_signal_element` and requires `E_select_*` before E3/E4/rank. Memory hints preserve the evidence chain. | `tests/test_runtime_plan.py`, `tests/test_multi_agent.py`, `tests/test_business_signal_policy.py` |",
    "| 3 | Plan compiler | `runtime/plan_compiler.py`, `business/discovery_policy.py`, `business/signal_policy.py`, `business/attribution_experience.py` | `ParsedIntent` compiles to typed `RcaPlan`; explicit slices keep `E1/E2/E3/E4/E_rank`, while broad discovery inserts first-class `select_signal_element`. Experience and memory may reorder the complete policy-approved lane set, but cannot remove lanes or change canonical E4 source ordering. | `tests/test_runtime_plan.py`, `tests/test_attribution_experience.py`, `tests/test_multi_agent.py`, `tests/test_business_signal_policy.py` |",
)

replace_once(
    "docs/COMPLIANCE_MATRIX.md",
    "| 8 | Memory boundary | `memory/*`, `runtime/memory_service.py`, `runtime/plan_models.py` | Memory is represented as `CasePrior` planning influence only; runtime memory read is traced when enabled, forbidden answer-bearing fields fail typed, and memory cannot become evidence or final conclusion. | `tests/test_memory.py`, `tests/test_runtime_run_service.py` |",
    "| 8 | Memory boundary | `memory/*`, `runtime/memory_service.py`, `runtime/plan_models.py`, `business/attribution_experience.py` | Memory is represented as `CasePrior` planning influence only. It may affect execution priority over existing policy lanes, but cannot change lane coverage, canonical E4 source ordering, evidence, contribution values, or the final conclusion. | `tests/test_memory.py`, `tests/test_attribution_experience.py`, `tests/test_runtime_plan.py`, `tests/test_runtime_run_service.py` |",
)

path = ROOT / "docs/reference/decisions.md"
source = path.read_text(encoding="utf-8")
if "## ADL-0055:" in source:
    raise RuntimeError("ADL-0055 already present")
entry = """## ADL-0055: attribution experience is priority-only and evidence-bounded

| Field | Value |
|---|---|
| Date | 2026-06-22 |
| Status | accepted |
| Scope | experience catalog, plan compiler, memory priority, interaction ranking |

### Decision

Add a strict attribution-experience catalog for generic hypotheses, evidence
branches, retention guidance, and residual-gap guidance. Resolve the complete
discovery lane set from `MetricPolicyRegistry` first. Experience and memory may
reorder that set for execution, but merge source aliases remain in canonical
policy order and the complete set is always executed.

Interaction selection is mechanism-specific. It requires current-run
`signal_type=interaction` evidence for both channel and category in the target
bad direction. Other signal types cannot verify an interaction candidate.

### Rationale

Separating canonical coverage from execution priority makes memory influence
monotonic and auditable. It also prevents evidence from one business mechanism
from being reused as proof for another mechanism.

### Rejected alternatives

A case-answer catalog, concrete element mappings, final-candidate bonuses, and
memory-controlled lane removal were rejected. A global interaction score bonus
was also rejected because it could misclassify independent mechanisms.

---

"""
path.write_text(entry + source, encoding="utf-8")
