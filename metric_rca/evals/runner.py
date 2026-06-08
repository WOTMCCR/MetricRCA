"""Eval runner —— Phase 1 占位实现。

完整的 eval（读 anomaly_ground_truth、逐 case 跑 RCA、打分、写 eval_run/eval_case_result、
输出 JSON+Markdown）属于 Phase >1。这里**故意**只打印 NOT IMPLEMENTED 并以非零退出码返回，
以满足"占位可存在，但绝不假成功"的反 shortcut 约束（contract / COMPLIANCE_MATRIX 第 23 行）。

非零退出码确保 `make eval` 不会被误判为通过。
"""

from __future__ import annotations


def main() -> int:
    print("NOT IMPLEMENTED (Phase >1)")
    return 1  # 非零：明确表示"未实现"，不冒充成功


if __name__ == "__main__":
    raise SystemExit(main())
