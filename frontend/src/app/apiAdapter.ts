import type {
  ApiError,
  EvalResponse,
  EvidenceResponse,
  MemoryResponse,
  RunSummary,
  SqlAuditResponse,
  TasksResponse,
  TraceResponse,
} from '../apiClient';
import type {
  Candidate,
  EvalSummary,
  Evidence,
  MemoryRec,
  NumericClaim,
  RunResponse,
  SqlAudit,
  TaskRec,
  TraceStep,
} from './components/mockData';

export type InvestigationBundle = {
  run: RunResponse;
  trace: TraceStep[];
  evidence: Evidence[];
  sqlAudit: SqlAudit[];
  memory: MemoryRec[];
  tasks: TaskRec[];
};

export type RunRequestContext = {
  question: string;
  targetDate?: string;
  businessToday?: string;
};

export function buildLoadingRun(question: string): RunResponse {
  return {
    run_id: 'pending',
    status: 'running',
    metric_id: 'pending',
    target_date: 'pending',
    business_today: 'pending',
    question,
    created_at: new Date().toISOString(),
    report: {
      narrative: 'Investigation is running.',
      numeric_claims: [],
    },
    candidates: [],
    links: {},
  };
}

export function toInvestigationBundle(
  run: RunSummary,
  trace: TraceResponse,
  evidence: EvidenceResponse,
  sqlAudit: SqlAuditResponse,
  tasks: TasksResponse,
  memory: MemoryResponse,
  context: RunRequestContext,
): InvestigationBundle {
  const uiRun = toRunResponse(run, context);
  const uiTrace = trace.trace.map(toTraceStep);
  return {
    run: uiRun,
    trace: uiTrace,
    evidence: evidence.evidence.map((row) => toEvidence(row, uiTrace)),
    sqlAudit: sqlAudit.sql_audit.map(toSqlAudit),
    tasks: tasks.tasks.map(toTask),
    memory: memory.memory.map(toMemory),
  };
}

export function toEvalSummary(response: EvalResponse | ApiError): EvalSummary {
  if ('error_code' in response) {
    return {
      eval_id: response.error_code,
      case_total: 0,
      top1_rate: 0,
      top3_rate: 0,
      anomaly_accuracy: 0,
      evidence_coverage_avg: 0,
      sql_safe_rate: 0,
      report_traceable_rate: 0,
      reflection_repair_ok: false,
      memory_pollution_ok: false,
      dangerous_sql_blocked: false,
      no_anomaly_correct: false,
      thresholds_met: false,
      cases: [
        {
          case_id: response.error_code,
          metric_id: 'eval',
          expected: 'typed success',
          actual: response.message,
          passed: false,
        },
      ],
    };
  }
  const summary = response.summary ?? {};
  const cases = response.cases ?? [];
  return {
    eval_id: response.eval_id ?? 'eval',
    case_total: numberValue(summary.case_total),
    top1_rate: numberValue(summary.top1_rate),
    top3_rate: numberValue(summary.top3_rate),
    anomaly_accuracy: numberValue(summary.anomaly_accuracy),
    evidence_coverage_avg: numberValue(summary.evidence_coverage_avg),
    sql_safe_rate: numberValue(summary.sql_safe_rate),
    report_traceable_rate: numberValue(summary.report_traceable_rate),
    reflection_repair_ok: Boolean(summary.reflection_repair_ok),
    memory_pollution_ok: Boolean(summary.memory_pollution_ok),
    dangerous_sql_blocked: Boolean(summary.dangerous_sql_blocked),
    no_anomaly_correct: Boolean(summary.no_anomaly_correct),
    thresholds_met: Boolean(summary.thresholds_met),
    cases: cases.map((row, index) => {
      const detail = isObject(row.detail) ? row.detail : {};
      const selected = isObject(detail.selected_candidate) ? detail.selected_candidate : {};
      return {
        case_id: stringValue(row.case_id, `case_${index + 1}`),
        metric_id: stringValue(detail.metric_id ?? row.metric_id, 'unknown'),
        expected: stringValue(detail.expected_metric ?? row.expected, 'expected'),
        actual: stringValue(selected.root_cause_type ?? detail.status ?? row.actual, 'none'),
        passed: Boolean(row.top1_ok ?? row.no_anomaly_task_ok ?? row.passed),
      };
    }),
  };
}

function toRunResponse(run: RunSummary, context: RunRequestContext): RunResponse {
  const report = run.report ?? {};
  const candidates = run.candidates.map((candidate, index) => toCandidate(candidate, run.run_id, index));
  const topCandidate =
    candidates[0] ??
    (isObject(report.top_candidate) ? toCandidate(report.top_candidate, run.run_id, 0) : undefined);
  const metricId = stringValue(report.metric_id, topCandidate?.root_cause_type ?? 'unknown');
  const targetDate = stringValue(report.target_date, context.targetDate ?? 'unknown');
  const status = normalizeStatus(run.status);
  return {
    run_id: run.run_id,
    status,
    metric_id: metricId,
    target_date: targetDate,
    business_today: context.businessToday ?? '2026-06-06',
    question: context.question,
    created_at: new Date().toISOString(),
    error: run.error_code
      ? {
          error_code: run.error_code,
          message: run.error_code,
          recoverable: false,
          retryable: false,
        }
      : undefined,
    report: {
      top_candidate: topCandidate,
      numeric_claims: Array.isArray(report.numeric_claims)
        ? report.numeric_claims.map(toNumericClaim)
        : [],
      narrative: narrativeFor(run, topCandidate),
    },
    candidates,
    links: run.links ?? {},
  };
}

function toCandidate(value: Record<string, unknown>, runId: string, index: number): Candidate {
  return {
    candidate_id: stringValue(value.candidate_id, `${runId}:candidate:${index + 1}`),
    root_cause_type: stringValue(value.root_cause_type, 'unknown'),
    dimension: stringValue(value.dimension, 'unknown'),
    element: stringValue(value.element, 'unknown'),
    verdict: normalizeVerdict(value.verdict),
    contribution_pct: percentValue(value.contribution_pct),
    eng_confidence: optionalNumber(value.eng_confidence),
    evidence_ids: Array.isArray(value.evidence_ids)
      ? value.evidence_ids.map((id) => String(id))
      : [],
    rationale: stringValue(value.rationale, ''),
  };
}

function toNumericClaim(value: unknown): NumericClaim {
  if (!isObject(value)) {
    return { name: 'claim', value: String(value), evidence_id: '' };
  }
  return {
    name: stringValue(value.name, 'claim'),
    value: typeof value.value === 'number' ? value.value : stringValue(value.value, ''),
    unit: typeof value.unit === 'string' ? value.unit : undefined,
    evidence_id: stringValue(value.evidence_id, ''),
  };
}

function toTraceStep(row: Record<string, unknown>): TraceStep {
  return {
    step_id: stringValue(row.step_id, `${row.run_id ?? 'run'}:${row.seq ?? 'step'}`),
    run_id: stringValue(row.run_id, ''),
    seq: numberValue(row.seq),
    node: stringValue(row.node, 'unknown'),
    action: stringValue(row.action, 'unknown'),
    input_summary: isObject(row.input_summary) ? row.input_summary : null,
    output_summary: isObject(row.output_summary) ? row.output_summary : null,
    error_code: typeof row.error_code === 'string' ? row.error_code : null,
    latency_ms: numberValue(row.latency_ms),
    created_at: stringValue(row.created_at, ''),
  };
}

function toEvidence(row: Record<string, unknown>, trace: TraceStep[]): Evidence {
  const evidenceId = stringValue(row.evidence_id, 'evidence');
  return {
    evidence_id: evidenceId,
    guard_status: normalizeGuardStatus(row.guard_status),
    sql_hash: stringValue(row.sql_hash, ''),
    sql_text: stringValue(row.sql_text, ''),
    query_spec: isObject(row.query_spec) ? row.query_spec : {},
    result_summary: isObject(row.result_summary) ? row.result_summary : {},
    data_source: stringValue(row.data_source, 'metric_repository'),
    created_at: stringValue(row.created_at, ''),
    produced_by_step: trace.find((step) => JSON.stringify(step.output_summary ?? {}).includes(evidenceId))?.step_id,
  };
}

function toSqlAudit(row: Record<string, unknown>): SqlAudit {
  return {
    audit_id: stringValue(row.audit_id, ''),
    guard_status: normalizeSqlGuardStatus(row.guard_status),
    sql_hash: stringValue(row.sql_hash, ''),
    sql_text: stringValue(row.sql_text, ''),
    row_count: numberValue(row.row_count),
    latency_ms: numberValue(row.latency_ms),
    guard_errors: Array.isArray(row.guard_errors) ? row.guard_errors.map(String) : [],
    created_at: stringValue(row.created_at, ''),
  };
}

function toTask(row: Record<string, unknown>): TaskRec {
  return {
    task_id: stringValue(row.task_id, ''),
    title: stringValue(row.title, 'Operation task'),
    root_cause_type: stringValue(row.root_cause_type, 'unknown'),
    payload: isObject(row.payload) ? row.payload : {},
    created_at: stringValue(row.created_at, ''),
  };
}

function toMemory(row: Record<string, unknown>): MemoryRec {
  return {
    step_id: stringValue(row.step_id, ''),
    node: stringValue(row.node, 'memory'),
    output_summary: isObject(row.output_summary) ? row.output_summary : {},
    error_code: typeof row.error_code === 'string' ? row.error_code : null,
    created_at: stringValue(row.created_at, ''),
  };
}

function narrativeFor(run: RunSummary, candidate?: Candidate): string {
  if (run.status === 'no_anomaly') {
    return 'No anomaly was detected. The run produced no root-cause candidate and no operation task.';
  }
  if (run.error_code) {
    return `Run failed with ${run.error_code}. No confirmed conclusion should be used.`;
  }
  if (!candidate) {
    return 'Run completed without a projected top candidate.';
  }
  return `${candidate.root_cause_type} is the top projected cause: ${candidate.dimension}=${candidate.element}, verdict=${candidate.verdict}.`;
}

function percentValue(value: unknown): number | undefined {
  if (typeof value !== 'number') {
    return undefined;
  }
  return Math.abs(value) <= 1 ? value * 100 : value;
}

function numberValue(value: unknown): number {
  if (typeof value === 'number') {
    return value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function optionalNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function stringValue(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value.length > 0) {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return fallback;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizeStatus(value: string): RunResponse['status'] {
  if (value === 'succeeded' || value === 'no_anomaly' || value === 'failed' || value === 'running') {
    return value;
  }
  return 'failed';
}

function normalizeVerdict(value: unknown): Candidate['verdict'] {
  if (value === 'confirmed' || value === 'likely' || value === 'insufficient' || value === 'ruled_out') {
    return value;
  }
  return 'insufficient';
}

function normalizeGuardStatus(value: unknown): Evidence['guard_status'] {
  if (value === 'passed' || value === 'rejected' || value === 'warning') {
    return value;
  }
  return 'warning';
}

function normalizeSqlGuardStatus(value: unknown): SqlAudit['guard_status'] {
  return value === 'passed' ? 'passed' : 'rejected';
}
