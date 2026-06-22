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
    "metric_rca/runtime/plan_compiler.py",
    '    if experience_advice is None or experience_advice.memory_mode != "priority_only":\n'
    "        return list(canonical_lanes)\n",
    "    if experience_advice is None:\n"
    "        return list(canonical_lanes)\n",
)

replace_once(
    "tests/test_runtime_plan.py",
    '    assert [(action.args["dimension"], action.args["signal_type"], action.produces[0]) for action in selections] == [\n'
    '        ("category", "inventory", "E_select_category"),\n'
    '        ("channel", "conversion", "E_select_channel_conv"),\n'
    "    ]\n",
    '    assert [(action.args["dimension"], action.args["signal_type"], action.produces[0]) for action in selections] == [\n'
    '        ("channel", "conversion", "E_select_channel_conv"),\n'
    '        ("category", "inventory", "E_select_category"),\n'
    "    ]\n",
)

replace_once(
    "tests/test_runtime_plan.py",
    '    assert [(action.args["dimension"], action.args["signal_type"], action.args["element"]) for action in signal_actions] == [\n'
    '        ("channel", "campaign", "paid_ads"),\n'
    '        ("category", "inventory", None),\n'
    '        ("channel", "conversion", None),\n'
    "    ]\n"
    '    assert [(action.produces, action.args["evidence_alias"]) for action in signal_actions] == [\n'
    '        (["E3_ch_campaign"], "E3_ch_campaign"),\n'
    '        (["E3_cat"], "E3_cat"),\n'
    '        (["E3_ch_conversion"], "E3_ch_conversion"),\n'
    "    ]\n"
    '    assert [action.dynamic for action in signal_actions] == [False, True, True]\n'
    '    assert [action.args["filters"] for action in signal_actions] == [{}, {}, {}]\n'
    '    assert signal_actions[2].args["element_selection"] == "signal_anomaly"\n'
    '    assert signal_actions[2].args["explicit_scope_policy"] == "global_explanatory"\n'
    '    assert [action.requires[-1] for action in contribution_actions] == [\n'
    '        "E3_ch_campaign",\n'
    '        "E3_cat",\n'
    '        "E3_ch_conversion",\n'
    "    ]\n"
    '    assert [(action.args["dimension"], action.args["element"], action.args["evidence_alias"]) for action in contribution_actions] == [\n'
    '        ("channel", "paid_ads", "E4_channel"),\n'
    '        ("category", None, "E4_category"),\n'
    '        ("channel", None, "E4_channel_conversion"),\n'
    "    ]\n"
    '    assert [action.args["filters"] for action in contribution_actions] == [{}, {"channel": "paid_ads"}, {}]\n'
    '    assert contribution_actions[2].args["explicit_scope_policy"] == "global_explanatory"\n',
    '    assert [(action.args["dimension"], action.args["signal_type"], action.args["element"]) for action in signal_actions] == [\n'
    '        ("channel", "campaign", "paid_ads"),\n'
    '        ("channel", "conversion", None),\n'
    '        ("category", "inventory", None),\n'
    "    ]\n"
    '    assert [(action.produces, action.args["evidence_alias"]) for action in signal_actions] == [\n'
    '        (["E3_ch_campaign"], "E3_ch_campaign"),\n'
    '        (["E3_ch_conversion"], "E3_ch_conversion"),\n'
    '        (["E3_cat"], "E3_cat"),\n'
    "    ]\n"
    '    assert [action.dynamic for action in signal_actions] == [False, True, True]\n'
    '    assert [action.args["filters"] for action in signal_actions] == [{}, {}, {}]\n'
    '    assert signal_actions[1].args["element_selection"] == "signal_anomaly"\n'
    '    assert signal_actions[1].args["explicit_scope_policy"] == "global_explanatory"\n'
    '    assert [action.requires[-1] for action in contribution_actions] == [\n'
    '        "E3_ch_campaign",\n'
    '        "E3_ch_conversion",\n'
    '        "E3_cat",\n'
    "    ]\n"
    '    assert [(action.args["dimension"], action.args["element"], action.args["evidence_alias"]) for action in contribution_actions] == [\n'
    '        ("channel", "paid_ads", "E4_channel"),\n'
    '        ("channel", None, "E4_channel_conversion"),\n'
    '        ("category", None, "E4_category"),\n'
    "    ]\n"
    '    assert [action.args["filters"] for action in contribution_actions] == [{}, {}, {"channel": "paid_ads"}]\n'
    '    assert contribution_actions[1].args["explicit_scope_policy"] == "global_explanatory"\n',
)

replace_once(
    "tests/test_runtime_plan.py",
    '    assert plan.scope_mode == "explicit_multi_driver"\n'
    '    assert "metric_rca/runtime/ranking.py" not in str(plan.model_dump(mode="json"))\n',
    '    assert plan.scope_mode == "explicit_multi_driver"\n'
    '    assert plan.experience_advice is not None\n'
    '    assert plan.experience_advice.memory_mode == "disabled"\n'
    '    assert [\n'
    '        (lane.dimension, lane.signal_type, lane.alias_discriminator)\n'
    '        for lane in plan.experience_advice.execution_lane_priority\n'
    '    ][:3] == [\n'
    '        ("channel", "campaign", None),\n'
    '        ("channel", "conversion", "conversion"),\n'
    '        ("category", "inventory", None),\n'
    '    ]\n'
    '    assert "metric_rca/runtime/ranking.py" not in str(plan.model_dump(mode="json"))\n',
)
