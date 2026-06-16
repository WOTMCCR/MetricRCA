import { FormEvent, useState } from 'react';
import type { ReactNode } from 'react';
import {
  HttpMetricRcaApiClient,
  MetricRcaApiClient,
  RunSummary,
  isApiError,
} from './apiClient';
import './styles.css';

type LoadedRun = {
  run: RunSummary;
  trace: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  sqlAudit: Array<Record<string, unknown>>;
  tasks: Array<Record<string, unknown>>;
  memory: Array<Record<string, unknown>>;
  evalStatus: Record<string, unknown> | null;
};

type TokenTotals = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
};

type AppProps = {
  apiClient?: MetricRcaApiClient;
  initialData?: LoadedRun;
};

const defaultClient = new HttpMetricRcaApiClient();

export function App({ apiClient = defaultClient, initialData }: AppProps) {
  const [question, setQuestion] = useState('Why did yesterday GMV drop?');
  const [loaded, setLoaded] = useState<LoadedRun | null>(initialData ?? null);
  const [status, setStatus] = useState<string>('idle');
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus('running');
    setError(null);
    try {
      const run = await apiClient.createRun({
        question,
        memory_enabled: false,
      });
      const [latestRun, trace, evidence, sqlAudit, tasks, memory, evalStatus] = await Promise.all([
        apiClient.getRun(run.run_id),
        apiClient.getTrace(run.run_id),
        apiClient.getEvidence(run.run_id),
        apiClient.getSqlAudit(run.run_id),
        apiClient.getTasks(run.run_id),
        apiClient.getMemory(run.run_id),
        Promise.resolve(loaded?.evalStatus ?? null),
      ]);
      setLoaded({
        run: latestRun,
        trace: trace.trace,
        evidence: evidence.evidence,
        sqlAudit: sqlAudit.sql_audit,
        tasks: tasks.tasks,
        memory: memory.memory,
        evalStatus: evalStatus,
      });
      setStatus('loaded');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'UI_REQUEST_FAILED');
      setStatus('failed');
    }
  }

  async function handleRunEval() {
    setStatus('running_eval');
    setError(null);
    try {
      const evalStatus = await apiClient.runEval();
      setLoaded((current) => ({
        ...(current ?? emptyLoadedRun()),
        evalStatus,
      }));
      setStatus('loaded');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'UI_EVAL_REQUEST_FAILED');
      setStatus('failed');
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>MetricRCA</h1>
          <p>Persisted evidence, trace, and report projection for metric incidents.</p>
        </div>
        <div className="run-status" aria-label="run status">
          {loaded?.run.status ?? status}
        </div>
      </header>

      <section className="workspace">
        <Panel title="Question Input">
          <form className="question-form" onSubmit={handleSubmit}>
            <label htmlFor="question">Question</label>
            <textarea
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="submit">Run RCA</button>
          </form>
          {error ? <p className="error">{error}</p> : null}
        </Panel>

        <Panel title="Conclusion">
          <KeyValueRows
            rows={{
              run_id: loaded?.run.run_id,
              status: loaded?.run.status,
              error_code: loaded?.run.error_code,
              report_status: readPath(loaded?.run.report, 'status'),
            }}
          />
        </Panel>

        <Panel title="Root Cause Top-K">
          <DataTable
            rows={loaded?.run.candidates ?? []}
            columns={['root_cause_type', 'dimension', 'element', 'verdict']}
          />
        </Panel>

        <Panel title="Adtributor Candidates">
          <DataTable
            rows={adtributorCandidates(loaded)}
            columns={['root_cause_type', 'dimension', 'element', 'verdict', 'explanatory_power', 'surprise_js']}
          />
        </Panel>

        <Panel title="Evidence Table">
          <DataTable rows={loaded?.evidence ?? []} columns={['evidence_id', 'guard_status', 'sql_hash']} />
        </Panel>

        <Panel title="SQL Audit Table">
          <DataTable rows={loaded?.sqlAudit ?? []} columns={['audit_id', 'guard_status', 'row_count']} />
        </Panel>

        <Panel title="Trace Timeline">
          <DataTable rows={loaded?.trace ?? []} columns={['seq', 'node', 'action', 'error_code']} />
        </Panel>

        <Panel title="Token/Latency Dashboard">
          <KeyValueRows rows={tokenSummary(loaded)} />
          <DataTable
            rows={tokenBreakdownRows(loaded)}
            columns={['seq', 'node', 'action', 'latency_ms', 'token_usage']}
          />
        </Panel>

        <Panel title="Reflection Issues">
          <ReflectionIssues trace={loaded?.trace ?? []} />
        </Panel>

        <Panel title="Memory Status">
          <DataTable rows={loaded?.memory ?? []} columns={['memory_id', 'layer', 'mem_key', 'confidence', 'source']} />
        </Panel>

        <Panel title="Memory Layers">
          <DataTable rows={memoryLayerRows(loaded?.memory ?? [])} columns={['layer', 'record_count', 'mem_keys']} />
        </Panel>

        <Panel title="Eval Summary">
          <button type="button" onClick={handleRunEval}>
            Run Eval
          </button>
          <EvalStatus value={loaded?.evalStatus ?? null} />
        </Panel>
      </section>
    </main>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function DataTable({ rows, columns }: { rows: Array<Record<string, unknown>>; columns: string[] }) {
  if (rows.length === 0) {
    return <p className="muted">No persisted rows.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column}>{column}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={String(row.id ?? row.step_id ?? row.evidence_id ?? row.audit_id ?? index)}>
            {columns.map((column) => (
              <td key={column}>{formatValue(row[column])}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KeyValueRows({ rows }: { rows: Record<string, unknown> }) {
  const entries = Object.entries(rows).filter(([, value]) => value !== undefined && value !== null);
  if (entries.length === 0) {
    return <p className="muted">No data loaded.</p>;
  }
  return (
    <dl>
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ReflectionIssues({ trace }: { trace: Array<Record<string, unknown>> }) {
  const rows = trace.filter((row) => row.node === 'reflection_verify');
  return <DataTable rows={rows} columns={['seq', 'error_code', 'output_summary']} />;
}

function EvalStatus({ value }: { value: Record<string, unknown> | null }) {
  if (value === null) {
    return <p className="muted">Eval status not loaded.</p>;
  }
  if (isApiError(value)) {
    return <p className="error">{value.error_code}</p>;
  }
  return <KeyValueRows rows={value.summary as Record<string, unknown>} />;
}

function adtributorCandidates(loaded: LoadedRun | null): Array<Record<string, unknown>> {
  if (loaded === null) {
    return [];
  }
  for (const evidence of loaded.evidence) {
    const summary = readPath(evidence, 'result_summary');
    const candidates = readPath(summary, 'candidates');
    if (!Array.isArray(candidates)) {
      continue;
    }
    const rows = candidates.filter(isRecord);
    if (rows.some((row) => row.explanatory_power !== undefined || row.surprise_js !== undefined)) {
      return [...rows].sort(compareAdtributorCandidate);
    }
  }
  return [...loaded.run.candidates].sort(compareAdtributorCandidate);
}

function compareAdtributorCandidate(a: Record<string, unknown>, b: Record<string, unknown>) {
  const epDelta = numericValue(b.explanatory_power) - numericValue(a.explanatory_power);
  if (epDelta !== 0) {
    return epDelta;
  }
  return numericValue(b.surprise_js) - numericValue(a.surprise_js);
}

function tokenSummary(loaded: LoadedRun | null): Record<string, unknown> {
  if (loaded === null) {
    return {};
  }
  if (isRecord(loaded.run.token_summary)) {
    const { by_step: _byStep, ...summary } = loaded.run.token_summary;
    return summary;
  }
  return loaded.trace.reduce<TokenTotals>(
    (acc, row) => {
      acc.latency_ms += numericValue(row.latency_ms);
      const usage = readPath(row, 'token_usage');
      if (isRecord(usage)) {
        acc.prompt_tokens += numericValue(usage.prompt_tokens);
        acc.completion_tokens += numericValue(usage.completion_tokens);
        acc.total_tokens += numericValue(usage.total_tokens);
      }
      return acc;
    },
    { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, latency_ms: 0 },
  );
}

function tokenBreakdownRows(loaded: LoadedRun | null): Array<Record<string, unknown>> {
  if (loaded === null) {
    return [];
  }
  if (isRecord(loaded.run.token_summary) && Array.isArray(loaded.run.token_summary.by_step)) {
    return loaded.run.token_summary.by_step.filter(isRecord);
  }
  return loaded.trace.map((row) => ({
    seq: row.seq,
    node: row.node,
    action: row.action,
    latency_ms: row.latency_ms,
    token_usage: row.token_usage,
  }));
}

function memoryLayerRows(memory: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const byLayer = new Map<string, string[]>();
  for (const row of memory) {
    const layer = String(row.layer ?? 'unknown');
    const memKey = String(row.mem_key ?? '');
    byLayer.set(layer, [...(byLayer.get(layer) ?? []), memKey]);
  }
  return [...byLayer.entries()].map(([layer, memKeys]) => ({
    layer,
    record_count: memKeys.length,
    mem_keys: memKeys.filter(Boolean).join(', '),
  }));
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null) {
    return '';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function readPath(value: unknown, key: string): unknown {
  if (typeof value !== 'object' || value === null) {
    return undefined;
  }
  return (value as Record<string, unknown>)[key];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function numericValue(value: unknown): number {
  if (typeof value === 'number') {
    return value;
  }
  if (typeof value === 'string' && value.trim() !== '') {
    return Number(value);
  }
  return 0;
}

function emptyLoadedRun(): LoadedRun {
  return {
    run: {
      run_id: '',
      status: 'idle',
      error_code: null,
      report: null,
      candidates: [],
      tasks: [],
    },
    trace: [],
    evidence: [],
    sqlAudit: [],
    tasks: [],
    memory: [],
    evalStatus: null,
  };
}
