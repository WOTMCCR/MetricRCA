# MetricRCA

MetricRCA is a metric root-cause analysis system with a deepagents
tool-calling core. The LLM is required for agent planning, but SQL, evidence,
attribution, reflection, report projection, API output, UI display, and eval
scoring are all controlled by code and persisted artifacts.

## Architecture

- Metadata is read from `metric_definition` through `MetadataRepository`.
- `RunOrchestrator` creates the run, invokes a deepagents expert, runs
  persisted-artifact Reflection, projects the report, and finalizes status.
- The only metric-fact data path is:
  `QuerySpec -> SQLRenderer -> SQLGuard -> MetricRepository.execute_plan`.
- `GuardMiddleware` validates the registered tool whitelist and `extra=forbid`
  args schemas, enforces budgets, writes trace, and requires data tools to
  return current-run evidence ids.
- Reflection is a deterministic verifier with one repair re-entry through the
  same deepagents thread and normal middleware/tool path.
- Memory can only reorder drilldown priority. It cannot become evidence or a
  final conclusion.
- Final report output is a verified projection. Numeric claims must bind
  persisted Evidence, currently E4.

## Zero Silent Fallback

The project intentionally fails fast with typed errors. It forbids broad
exception swallowing, route-hardcoded RCA output, eval hardcoded success,
LLM-written SQL/facts/root causes, memory-derived conclusions, and report
generation after failed Reflection.

Bounded SQL retry remains intentionally deferred; SQL execution failure is
currently a typed fail-fast `SQL_EXECUTION_FAILED` path.

## Commands

```bash
PATH=.venv/bin:$PATH make up
PATH=.venv/bin:$PATH make seed
PATH=.venv/bin:$PATH make api
PATH=.venv/bin:$PATH make ui
PATH=.venv/bin:$PATH make eval
PATH=.venv/bin:$PATH make eval-http BASE_URL=http://127.0.0.1:8000 PROVIDER=openai MODEL=gpt-5-nano
PATH=.venv/bin:$PATH make test
npm test --prefix frontend -- --run
npm run build --prefix frontend
```

Make targets map to `docker compose`, `python`, `uvicorn`, `npm`, and `pytest`
commands as defined in `Makefile`. Local API and eval targets prefix their
commands with `LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false` so external
LangSmith network ingestion cannot affect persisted-artifact verification.

## API

FastAPI is exposed by `metric_rca.api.main:app`.

- `GET /health`
- `POST /api/rca/runs`
- `GET /api/rca/runs/{run_id}`
- `GET /api/rca/runs/{run_id}/trace`
- `GET /api/rca/runs/{run_id}/evidence`
- `GET /api/rca/runs/{run_id}/sql-audit`
- `GET /api/rca/runs/{run_id}/tasks`
- `GET /api/rca/runs/{run_id}/memory`
- `POST /api/evals/run`
- `POST /api/evals/{eval_id}/summary`
- `POST /api/evals/{eval_id}/case-results`
- `GET /api/evals/{eval_id}`

`POST /api/rca/runs` invokes `run_rca()`. `GET /api/rca/runs/{run_id}` reads
persisted artifacts and reconstructs the report through
`metric_rca.reporting.projector`; it does not depend on graph return state.

Unified business/system error responses contain:

```json
{
  "error_code": "SYSTEM_TABLE_READ_FAILED",
  "message": "system table read failed",
  "recoverable": false,
  "retryable": false,
  "trace_step_id": null,
  "suggested_next_action": null
}
```

Representative error codes include `SYSTEM_TABLE_READ_FAILED`,
`REPORT_ARTIFACT_MISSING`, `RUN_NOT_FOUND`, `EVAL_NOT_FOUND`,
`EVAL_GROUND_TRUTH_MISSING`, `REFLECTION_REPAIR_FAILED`,
`SQL_GUARD_REJECTED`, `SQL_EXECUTION_FAILED`, `LLM_REQUIRED_UNAVAILABLE`,
`MEMORY_READ_FAILED`, and `MEMORY_WRITE_FAILED`.

## UI

The UI is a separated React/Vite frontend in `frontend/`. It uses an injectable
API client and browser `fetch`, not direct graph imports. The dashboard renders
P8 observability panels:

1. Question input
2. Conclusion/report
3. Root cause Top-K
4. Adtributor candidates
5. Evidence table
6. SQL audit table
7. Trace timeline
8. Token/latency dashboard
9. Reflection issues
10. Memory status
11. Memory layers
12. Eval summary

Set `VITE_METRIC_RCA_API_BASE_URL` when the API is not running at
`http://127.0.0.1:8000`.

## Eval

`make eval` runs the 20 persisted-artifact cases in `metric_rca/evals/cases.jsonl`. The
runner reads authoritative answers from `anomaly_ground_truth`, calls `run_rca`
once per memory-enabled case and once per memory-disabled baseline case, then
scores persisted artifacts:

- `agent_run`
- `evidence`
- `trace_step`
- `sql_audit`
- `operation_task`
- reconstructed report from `reporting.projector`

Summary fields produced:

- `case_total`
- `top1_rate`
- `top3_rate`
- `anomaly_accuracy`
- `evidence_coverage_avg`
- `sql_safe_rate`
- `report_traceable_rate`
- `reflection_repair_ok`
- `memory_pollution_ok`
- `dangerous_sql_blocked`
- `no_anomaly_correct`

`dangerous_sql_blocked` is computed by calling the real SQLGuard on dangerous
SQL. It is a boolean and is never a constant placeholder.

## Target Response Shape

```json
{
  "run_id": "run-...",
  "status": "succeeded",
  "error_code": null,
  "report": {
    "status": "succeeded",
    "metric_id": "gmv",
    "target_date": "2026-06-05",
    "top_candidate": {
      "root_cause_type": "campaign_traffic_drop",
      "dimension": "channel",
      "element": "paid_ads",
      "verdict": "confirmed"
    },
    "evidence_ids": ["run-...:E1", "run-...:E2", "run-...:E3", "run-...:E4"],
    "numeric_claims": [
      {"name": "contribution_pct", "value": 0.9, "evidence_id": "run-...:E4"}
    ]
  },
  "candidates": [
    {
      "root_cause_type": "campaign_traffic_drop",
      "dimension": "channel",
      "element": "paid_ads",
      "verdict": "confirmed"
    }
  ],
  "tasks": [],
  "links": {}
}
```

## Known Limitations

- Bounded SQL retry is deferred; SQL execution failures are typed fail-fast.
- Screenshots are not committed as fake placeholders. See `screenshots/README.md`
  for reproducible capture commands.
