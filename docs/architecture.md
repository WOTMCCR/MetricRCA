# MetricRCA Architecture

## Logical Architecture

```mermaid
flowchart LR
  User[User question] --> API[FastAPI]
  API --> RunService[RunService]
  RunService --> Intent[OpenAI Agents SDK intent agent]
  RunService --> RuntimeMemory[RuntimeMemoryService]
  RuntimeMemory --> Prior[CasePrior]
  Intent --> Compiler[RcaPlanCompiler]
  Prior --> Compiler
  Compiler --> Executor[RcaPlanExecutor]
  Executor --> Gate[ActionGate]
  Executor --> Tools[ToolExecutor]
  Tools --> Selection[E_select evidence]
  Selection --> Tools
  Tools --> QuerySpec[QuerySpec]
  QuerySpec --> Renderer[SQLRenderer]
  Renderer --> Guard[SQLGuard]
  Guard --> Repo[MetricRepository.execute_plan]
  Repo --> Evidence[(evidence/sql_audit)]
  Executor --> Trace[(trace_step token_usage)]
  RunService --> Memory[(memory_record)]
  RunService --> Reflection[Persisted Reflection]
  API --> Projector[Persisted report projector]
  Projector --> UI[React UI]
```

## Runtime Control Flow

```mermaid
flowchart TD
  START --> create_run
  create_run --> parse_intent
  parse_intent --> memory_read
  memory_read --> compile_plan
  compile_plan --> execute_plan
  execute_plan --> action_gate
  action_gate --> deterministic_tools
  deterministic_tools --> persisted_evidence
  persisted_evidence --> reflection
  reflection --> report_projection
  report_projection --> memory_write
  memory_write --> finish_run
  reflection --> failed_run
  failed_run --> failure_memory_write
  failure_memory_write --> END
  failed_run --> END
  finish_run --> END
```

## Deterministic Plan Execution

```mermaid
sequenceDiagram
  participant R as RunService
  participant M as RuntimeMemoryService
  participant C as RcaPlanCompiler
  participant E as RcaPlanExecutor
  participant G as ActionGate
  participant T as ToolExecutor
  participant Q as QuerySpec/Renderer/Guard
  participant DB as MetricRepository
  R->>M: read_priors(run_id, ParsedIntent)
  M-->>R: CasePrior planning hints
  R->>C: ParsedIntent + CasePrior
  C-->>R: RcaPlan
  R->>E: RunContext + RcaPlan
  E->>G: validate action
  G-->>E: GateDecision
  E->>T: execute allowed action
  T->>Q: build controlled query
  Q->>DB: execute guarded SQLPlan
  DB-->>T: rows + sql_audit
  T-->>E: ToolExecutionResult + Evidence IDs + sql_count
  E->>DB: compare sql_audit delta
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
  ESelect[(evidence E_select result_summary.selected_element)] --> Projector
  Tasks[(operation_task)] --> Projector
  Trace[(trace_step)] --> API
  Projector --> Report[Verified projection]
  Report --> Numeric[numeric_claims bound to E4]
  Report --> Candidate[identity-only top_candidate]
```

## Memory Boundary

```mermaid
flowchart TD
  Memory[(memory_record)] --> Prior[CasePrior]
  Prior --> Compiler[RcaPlanCompiler]
  Compiler --> CurrentEvidence[current-run E1-E4]
  CurrentEvidence --> Candidate[RootCauseCandidate]
  Memory -. forbidden .-> Candidate
  Memory -. forbidden .-> Evidence[evidence_id]
```

## API/UI Flow

```mermaid
flowchart LR
  ReactUI[React/Vite UI] --> API[FastAPI]
  API --> POST[POST /api/rca/runs]
  POST --> Runner[RunService run_rca]
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
  RunnerCall --> Artifacts[(agent_run/evidence/trace/sql_audit/tasks)]
  Artifacts --> Scorer[scorer]
  Scorer --> Guard[real SQLGuard dangerous_sql_blocked]
  Scorer --> EvalRows[(eval_run/eval_case_result)]
  Scorer --> Outputs[JSON + Markdown]
```
