# MetricRCA Architecture

## Logical Architecture

```mermaid
flowchart LR
  User[User question] --> API[FastAPI]
  API --> Graph[LangGraph StateGraph]
  Graph --> Intent[MetricService + OpenAI structured output]
  Graph --> React[Deterministic ReAct policy]
  React --> Tools[P2 tools]
  Tools --> QuerySpec[QuerySpec]
  QuerySpec --> Renderer[SQLRenderer]
  Renderer --> Guard[SQLGuard]
  Guard --> Repo[MetricRepository.execute_plan]
  Repo --> Evidence[(evidence/sql_audit)]
  Graph --> Trace[(trace_step/agent_run)]
  Graph --> Memory[(memory_record)]
  API --> Projector[Persisted report projector]
  Projector --> UI[React UI]
```

## Graph Control Flow

```mermaid
flowchart TD
  START --> parse_question
  parse_question --> read_memory
  read_memory --> plan_init
  plan_init --> react_step
  react_step --> execute_tool
  execute_tool --> react_step
  react_step --> attribute_rank
  attribute_rank --> reflection_verify
  reflection_verify --> generate_report
  reflection_verify --> react_step
  reflection_verify --> error_return
  generate_report --> create_tasks
  generate_report --> write_memory
  create_tasks --> write_memory
  error_return --> write_memory
  write_memory --> END
```

## ReAct Repair Path

```mermaid
sequenceDiagram
  participant R as reflection_verify
  participant A as react_step
  participant T as execute_tool
  participant Q as QuerySpec/Renderer/Guard
  participant DB as MetricRepository
  R->>A: ReflectionIssue.suggested_action
  A->>T: validated AgentAction
  T->>Q: build controlled query
  Q->>DB: execute guarded SQLPlan
  DB-->>T: rows + sql_audit
  T-->>R: new current-run Evidence
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
  Memory[(memory_record)] --> ReadMemory[read_memory]
  ReadMemory --> Priority[drilldown priority only]
  Priority --> ReAct[ReAct planning]
  ReAct --> CurrentEvidence[current-run E1-E4]
  CurrentEvidence --> Candidate[RootCauseCandidate]
  Memory -. forbidden .-> Candidate
  Memory -. forbidden .-> Evidence[evidence_id]
```

## API/UI Flow

```mermaid
flowchart LR
  ReactUI[React/Vite UI] --> API[FastAPI]
  API --> POST[POST /api/rca/runs]
  POST --> Graph[run_rca]
  API --> GET[GET persisted artifacts]
  GET --> Projector[reporting.projector]
  Projector --> ReactUI
```

## Eval Pipeline

```mermaid
flowchart TD
  Cases[cases.jsonl] --> Runner[eval runner]
  GT[(anomaly_ground_truth)] --> Runner
  Runner --> Graph[run_rca once per case]
  Graph --> Artifacts[(agent_run/evidence/trace/sql_audit/tasks)]
  Artifacts --> Scorer[scorer]
  Scorer --> Guard[real SQLGuard dangerous_sql_blocked]
  Scorer --> EvalRows[(eval_run/eval_case_result)]
  Scorer --> Outputs[JSON + Markdown]
```
