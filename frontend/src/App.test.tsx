import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';
import App from './app/App';
import type { InvestigationBundle } from './app/apiAdapter';
import type { MetricRcaApiClient, RunPayload } from './apiClient';

describe('MetricRCA investigation console', () => {
  afterEach(() => cleanup());

  test('renders the demo console shell with persisted artifact navigation', () => {
    render(<App apiClient={fakeClient()} initialData={bundle()} />);

    expect(screen.getByText('MetricRCA')).toBeInTheDocument();
    expect(screen.getByText('Investigation Console')).toBeInTheDocument();
    for (const label of ['Investigation', 'Candidates', 'Evidence', 'SQL Audit', 'Reflection', 'Tasks']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Memory' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Quality Eval' })).not.toBeInTheDocument();
    expect(screen.getByText('Generated RCA Report')).toBeInTheDocument();
    expect(screen.getByText('report generated')).toBeInTheDocument();
    expect(screen.getByText('Bound evidence')).toBeInTheDocument();
    expect(screen.getAllByText('guard passed').length).toBeGreaterThan(0);
    expect(screen.getByText('RCA Execution Path')).toBeInTheDocument();
    expect(screen.getAllByText('succeeded').length).toBeGreaterThan(0);
  });

  test('loads a real run through the injected API client', async () => {
    const client = fakeClient();
    render(<App apiClient={client} />);

    await waitFor(() => expect(screen.getAllByText('paid_ads').length).toBeGreaterThan(0));

    expect(client.calls).toEqual([
      'createRun:Why did yesterday GMV drop?',
      'getRun:run-1',
      'getTrace:run-1',
      'getEvidence:run-1',
      'getSqlAudit:run-1',
      'getTasks:run-1',
    ]);
  });

  test('submits a metric issue from the main diagnose panel', async () => {
    const client = fakeClient();
    render(<App apiClient={client} initialData={bundle()} />);

    await userEvent.clear(screen.getByLabelText('Metric issue'));
    await userEvent.type(screen.getByLabelText('Metric issue'), 'Why did yesterday refund rate increase?');
    await userEvent.click(screen.getByRole('button', { name: 'Diagnose' }));

    await waitFor(() => expect(client.calls[0]).toBe('createRun:Why did yesterday refund rate increase?'));
    expect(client.calls).toContain('getEvidence:run-1');
  });

  test('run history selector reloads the selected run artifacts', async () => {
    const client = fakeClient({ createdRunId: 'run-2' });
    render(<App apiClient={client} initialData={bundle('run-1', 'paid_ads')} />);

    await userEvent.click(screen.getByRole('button', { name: 'Diagnose' }));
    await waitFor(() => expect(screen.getAllByText('organic').length).toBeGreaterThan(0));

    await userEvent.selectOptions(screen.getByLabelText('Run history'), 'run-1');

    await waitFor(() => expect(screen.getAllByText('paid_ads').length).toBeGreaterThan(0));
    expect(client.calls.slice(-5)).toEqual([
      'getRun:run-1',
      'getTrace:run-1',
      'getEvidence:run-1',
      'getSqlAudit:run-1',
      'getTasks:run-1',
    ]);
  });

  test('switches to candidate and evidence views with real data', async () => {
    const client = fakeClient();
    render(<App apiClient={client} initialData={bundle()} />);

    await userEvent.click(screen.getByRole('button', { name: 'Candidates' }));
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getAllByText('run-1:E1').length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole('button', { name: 'Evidence' }));
    expect(screen.getAllByText('run-1:E4').length).toBeGreaterThan(0);
    expect(screen.getByText('guard passed')).toBeInTheDocument();
  });

  test('surfaces typed request failures without rendering stale success data', async () => {
    const client = fakeClient({ createRunError: new Error('LLM_REQUIRED_UNAVAILABLE:intent planner is required') });
    render(<App apiClient={client} />);

    expect(await screen.findByText('LLM_REQUIRED_UNAVAILABLE:intent planner is required')).toBeInTheDocument();
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);
    expect(screen.queryByText('paid_ads')).not.toBeInTheDocument();
  });
});

function fakeClient(options: { createRunError?: Error; createdRunId?: string } = {}): MetricRcaApiClient & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async createRun(payload: RunPayload) {
      calls.push(`createRun:${payload.question}`);
      if (options.createRunError) {
        throw options.createRunError;
      }
      return runSummary(options.createdRunId ?? 'run-1');
    },
    async getRun(runId: string) {
      calls.push(`getRun:${runId}`);
      return runSummary(runId);
    },
    async getTrace(runId: string) {
      calls.push(`getTrace:${runId}`);
      return { run_id: runId, trace: bundle(runId).trace };
    },
    async getEvidence(runId: string) {
      calls.push(`getEvidence:${runId}`);
      return { run_id: runId, evidence: bundle(runId).evidence };
    },
    async getSqlAudit(runId: string) {
      calls.push(`getSqlAudit:${runId}`);
      return { run_id: runId, sql_audit: bundle(runId).sqlAudit };
    },
    async getTasks(runId: string) {
      calls.push(`getTasks:${runId}`);
      return { run_id: runId, tasks: bundle(runId).tasks };
    },
    async getMemory(runId: string) {
      calls.push(`getMemory:${runId}`);
      return { run_id: runId, memory: bundle(runId).memory };
    },
    async runEval() {
      calls.push('runEval');
      return {
        eval_id: 'eval-1',
        summary: {
          case_total: 5,
          top1_rate: 1,
          top3_rate: 1,
          anomaly_accuracy: 1,
          evidence_coverage_avg: 1,
          sql_safe_rate: 1,
          report_traceable_rate: 1,
          reflection_repair_ok: true,
          memory_pollution_ok: true,
          dangerous_sql_blocked: true,
          no_anomaly_correct: true,
          thresholds_met: true,
        },
        cases: [{ case_id: 'gmv_paid_ads_drop', top1_ok: 1, detail: { metric_id: 'gmv', status: 'succeeded' } }],
      };
    },
  };
}

function elementForRun(runId: string) {
  return runId === 'run-2' ? 'organic' : 'paid_ads';
}

function runSummary(runId = 'run-1') {
  const element = elementForRun(runId);
  return {
    run_id: runId,
    status: 'succeeded',
    error_code: null,
    report: {
      status: 'succeeded',
      metric_id: 'gmv',
      target_date: '2026-06-05',
      top_candidate: {
        root_cause_type: 'campaign_traffic_drop',
        dimension: 'channel',
        element,
        verdict: 'confirmed',
      },
      numeric_claims: [{ name: 'contribution_pct', value: 1, evidence_id: `${runId}:E4` }],
    },
    candidates: [
      {
        root_cause_type: 'campaign_traffic_drop',
        dimension: 'channel',
        element,
        verdict: 'confirmed',
        contribution_pct: 1,
        eng_confidence: 1,
        evidence_ids: [`${runId}:E1`, `${runId}:E2`, `${runId}:E3`, `${runId}:E4`],
      },
    ],
    tasks: [{ task_id: 'task-1', title: 'Investigate campaign_traffic_drop' }],
    links: { self: `/api/rca/runs/${runId}`, trace: `/api/rca/runs/${runId}/trace` },
  };
}

function bundle(runId = 'run-1', element = elementForRun(runId)): InvestigationBundle {
  return {
    run: {
      run_id: runId,
      status: 'succeeded',
      metric_id: 'gmv',
      target_date: '2026-06-05',
      business_today: '2026-06-06',
      question: 'Why did yesterday GMV drop?',
      created_at: '2026-06-09T00:00:00Z',
      report: {
        top_candidate: {
          candidate_id: `${runId}:candidate:1`,
          root_cause_type: 'campaign_traffic_drop',
          dimension: 'channel',
          element,
          verdict: 'confirmed',
          contribution_pct: 100,
          eng_confidence: 1,
          evidence_ids: [`${runId}:E1`, `${runId}:E2`, `${runId}:E3`, `${runId}:E4`],
        },
        numeric_claims: [{ name: 'contribution_pct', value: 1, evidence_id: `${runId}:E4` }],
        narrative: 'campaign_traffic_drop is the top projected cause.',
      },
      candidates: [
        {
          candidate_id: `${runId}:candidate:1`,
          root_cause_type: 'campaign_traffic_drop',
          dimension: 'channel',
          element,
          verdict: 'confirmed',
          contribution_pct: 100,
          eng_confidence: 1,
          evidence_ids: [`${runId}:E1`, `${runId}:E2`, `${runId}:E3`, `${runId}:E4`],
        },
      ],
      links: { self: `/api/rca/runs/${runId}`, trace: `/api/rca/runs/${runId}/trace` },
    },
    trace: [
      {
        step_id: 's1',
        run_id: runId,
        seq: 1,
        node: 'parse_question',
        action: 'parse_question',
        input_summary: {},
        output_summary: {},
        error_code: null,
        latency_ms: 1,
        created_at: '2026-06-09T00:00:00Z',
      },
      {
        step_id: 's2',
        run_id: runId,
        seq: 2,
        node: 'reflection_verify',
        action: 'reflection_verify',
        input_summary: {},
        output_summary: { passed: true, issues: [], repair_count: 0 },
        error_code: null,
        latency_ms: 1,
        created_at: '2026-06-09T00:00:01Z',
      },
    ],
    evidence: ['E1', 'E2', 'E3', 'E4'].map((alias) => ({
      evidence_id: `${runId}:${alias}`,
      guard_status: 'passed' as const,
      sql_hash: `abc-${alias}`,
      sql_text: 'SELECT business_date, gmv FROM fact_order LIMIT 1',
      query_spec: { metric_id: 'gmv', purpose: alias === 'E4' ? 'contribution' : 'baseline' },
      result_summary: alias === 'E4'
        ? { selected_candidate: { element }, contribution_pct: 1 }
        : { current: 100, baseline_mean: 120 },
      data_source: 'fact_order',
      created_at: '2026-06-09T00:00:00Z',
    })),
    sqlAudit: [
      {
        audit_id: '1',
        guard_status: 'passed',
        sql_hash: 'abc',
        sql_text: 'SELECT business_date, gmv FROM fact_order LIMIT 1',
        row_count: 1,
        latency_ms: 1,
        guard_errors: [],
        created_at: '2026-06-09T00:00:00Z',
      },
    ],
    tasks: [
      {
        task_id: 'task-1',
        title: 'Investigate campaign_traffic_drop',
        root_cause_type: 'campaign_traffic_drop',
        payload: {},
        created_at: '2026-06-09T00:00:00Z',
      },
    ],
    memory: [
      {
        step_id: 'm1',
        node: 'read_memory',
        output_summary: { hits: 0 },
        error_code: null,
        created_at: '2026-06-09T00:00:00Z',
      },
    ],
  };
}
