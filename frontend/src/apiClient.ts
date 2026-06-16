export type RunSummary = {
  run_id: string;
  status: string;
  error_code: string | null;
  report: Record<string, unknown> | null;
  candidates: Array<Record<string, unknown>>;
  tasks: Array<Record<string, unknown>>;
  token_summary?: Record<string, unknown> | null;
  links?: Record<string, string>;
};

export type TraceResponse = { run_id: string; trace: Array<Record<string, unknown>> };
export type EvidenceResponse = { run_id: string; evidence: Array<Record<string, unknown>> };
export type SqlAuditResponse = { run_id: string; sql_audit: Array<Record<string, unknown>> };
export type TasksResponse = { run_id: string; tasks: Array<Record<string, unknown>> };
export type MemoryResponse = { run_id: string; memory: Array<Record<string, unknown>> };
export type EvalResponse = { eval_id?: string; summary?: Record<string, unknown>; cases?: Array<Record<string, unknown>> };
export type ApiError = {
  error_code: string;
  message: string;
  recoverable: boolean;
  retryable: boolean;
  trace_step_id: string | null;
  suggested_next_action: string | null;
};

export type RunPayload = {
  question: string;
  target_date?: string;
  business_today?: string;
  memory_enabled?: boolean;
  memory_required?: boolean;
  llm_provider?: string;
  llm_model?: string;
  llm_api_key?: string;
};

export interface MetricRcaApiClient {
  createRun(payload: RunPayload): Promise<RunSummary>;
  getRun(runId: string): Promise<RunSummary>;
  getTrace(runId: string): Promise<TraceResponse>;
  getEvidence(runId: string): Promise<EvidenceResponse>;
  getSqlAudit(runId: string): Promise<SqlAuditResponse>;
  getTasks(runId: string): Promise<TasksResponse>;
  getMemory(runId: string): Promise<MemoryResponse>;
  runEval(): Promise<EvalResponse | ApiError>;
}

export class HttpMetricRcaApiClient implements MetricRcaApiClient {
  private readonly baseUrl: string;

  constructor(baseUrl = import.meta.env.VITE_METRIC_RCA_API_BASE_URL ?? 'http://127.0.0.1:8000') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  createRun(payload: RunPayload): Promise<RunSummary> {
    return this.request('/api/rca/runs', { method: 'POST', body: JSON.stringify(payload) });
  }

  getRun(runId: string): Promise<RunSummary> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}`);
  }

  getTrace(runId: string): Promise<TraceResponse> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}/trace`);
  }

  getEvidence(runId: string): Promise<EvidenceResponse> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}/evidence`);
  }

  getSqlAudit(runId: string): Promise<SqlAuditResponse> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}/sql-audit`);
  }

  getTasks(runId: string): Promise<TasksResponse> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}/tasks`);
  }

  getMemory(runId: string): Promise<MemoryResponse> {
    return this.request(`/api/rca/runs/${encodeURIComponent(runId)}/memory`);
  }

  runEval(): Promise<EvalResponse | ApiError> {
    return this.request('/api/evals/run', { method: 'POST' }, { allowApiError: true });
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    options: { allowApiError?: boolean } = {},
  ): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        'content-type': 'application/json',
        ...(init.headers ?? {}),
      },
    });
    const body = await response.json();
    if (!response.ok) {
      if (isApiError(body)) {
        if (options.allowApiError === true) {
          return body as T;
        }
        throw new Error(`${body.error_code}:${body.message}`);
      }
      throw new Error(`API_REQUEST_FAILED:${response.status}`);
    }
    return body as T;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as ApiError).error_code === 'string' &&
    typeof (value as ApiError).message === 'string'
  );
}
