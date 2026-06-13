# MetricRCA Architecture

## Logical Architecture

```mermaid
flowchart LR
  User[User question] --> API[FastAPI]
  API --> Orchestrator[RunOrchestrator]
  Orchestrator --> DeepAgent[deepagents expert]
  DeepAgent --> Middleware[GuardMiddleware]
  Middleware --> Tools[registered MetricRCA tools]
  Tools --> QuerySpec[QuerySpec]
  QuerySpec --> Renderer[SQLRenderer]
  Renderer --> Guard[SQLGuard]
  Guard --> Repo[MetricRepository.execute_plan]
  Repo --> Evidence[(evidence/sql_audit)]
  Middleware --> Trace[(trace_step token_usage)]
  Orchestrator --> Memory[(memory_record)]
  Orchestrator --> Reflection[Persisted Reflection]
  API --> Projector[Persisted report projector]
  Projector --> UI[React UI]
```

## Orchestrator Control Flow

```mermaid
flowchart TD
  START --> create_run
  create_run --> build_agent
  build_agent --> deepagents_loop
  deepagents_loop --> guard_middleware
  guard_middleware --> tools
  tools --> persisted_evidence
  persisted_evidence --> reflection
  reflection --> repair_reentry
  repair_reentry --> deepagents_loop
  reflection --> report_projection
  report_projection --> memory_write
  memory_write --> finish_run
  reflection --> failed_run
  failed_run --> END
  finish_run --> END
```

## deepagents Repair Path

```mermaid
sequenceDiagram
  participant O as RunOrchestrator
  participant A as deepagents expert
  participant M as GuardMiddleware
  participant Q as QuerySpec/Renderer/Guard
  participant DB as MetricRepository
  O->>A: repair message with ReflectionIssue
  A->>M: tool call
  M->>M: whitelist, schema, budget
  M->>Q: build controlled query
  Q->>DB: execute guarded SQLPlan
  DB-->>M: rows + sql_audit
  M-->>O: new current-run Evidence
```

## QuerySpec Data Path

```mermaid
flowchart LR
  Tool[Tool args] --> Spec[QuerySpec]
  Spec --> Render[SQLRenderer]
  Render --> Plan[SQLPlan + renderer signature]
  Plan --> Guard[SQLGuard AST validation]
  Guard --> Signed[guard-signed SQLPlan]
  Signed --> Repo[MetricRepository.execute_plan]
  Repo --> Audit[(sql_audit)]
  Repo --> Evidence[(evidence)]
```

## Persisted Report Reconstruction

```mermaid
flowchart LR
  Run[(agent_run)] --> Projector
  E4[(evidence E4 result_summary.selected_candidate)] --> Projector
  Tasks[(operation_task)] --> Projector
  Trace[(trace_step)] --> API
  Projector --> Report[Verified projection]
  Report --> Numeric[numeric_claims bound to E4]
  Report --> Candidate[identity-only top_candidate]
```

## Memory Boundary

```mermaid
flowchart TD
  Memory[(memory_record)] --> Orchestrator[RunOrchestrator]
  Orchestrator --> Priority[planning priority only]
  Priority --> Agent[deepagents planning]
  Agent --> CurrentEvidence[current-run E1-E4]
  CurrentEvidence --> Candidate[RootCauseCandidate]
  Memory -. forbidden .-> Candidate
  Memory -. forbidden .-> Evidence[evidence_id]
```

## API/UI Flow

```mermaid
flowchart LR
  ReactUI[React/Vite UI] --> API[FastAPI]
  API --> POST[POST /api/rca/runs]
  POST --> Runner[RunOrchestrator run_rca]
  API --> GET[GET persisted artifacts]
  GET --> Projector[reporting.projector]
  Projector --> ReactUI
```

## Eval Pipeline

```mermaid
flowchart TD
  Cases[cases.jsonl] --> Runner[eval runner]
  GT[(anomaly_ground_truth)] --> Runner
  Runner --> RunnerCall[run_rca once per case]
  Graph --> Artifacts[(agent_run/evidence/trace/sql_audit/tasks)]
  Artifacts --> Scorer[scorer]
  Scorer --> Guard[real SQLGuard dangerous_sql_blocked]
  Scorer --> EvalRows[(eval_run/eval_case_result)]
  Scorer --> Outputs[JSON + Markdown]
```
