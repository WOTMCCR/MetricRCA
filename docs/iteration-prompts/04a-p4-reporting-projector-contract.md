# Prompt 4A - Reporting Projector Contract

```text
This mini-contract is binding for Matrix P4.

Implement metric_rca/reporting/projector.py as a pure deterministic artifact
projection layer.

Inputs:
- agent_run row
- evidence rows for run
- operation_task rows for run
- optional trace rows

Forbidden:
- no fact table reads
- no QuerySpec rendering
- no SQLGuard invocation
- no run_rca invocation
- no anomaly_ground_truth reads
- no hardcoded RCA output
- no LLM calls

Required:
- succeeded report must be reconstructed from persisted E4.result_summary.selected_candidate
- no_anomaly report must have no candidate/task and exactly E1
- failed run has no report
- numeric_claims must bind persisted evidence_id
- top_candidate must expose only root_cause_type, dimension, element, verdict
- projector must fail closed on missing/malformed persisted artifacts

Proof tests:
- test_projector_builds_report_from_persisted_e4_selected_candidate
- test_projector_rejects_missing_e4
- test_projector_rejects_malformed_e4
- test_projector_no_unverified_numeric_fields
- test_projector_no_anomaly_e1_only
- test_projector_failed_run_no_report
```
