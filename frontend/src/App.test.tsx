import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';
import { App } from './App';
import type { MetricRcaApiClient, RunPayload } from './apiClient';

describe('MetricRCA UI', () => {
  afterEach(() => cleanup());

  test('renders twelve required persisted-artifact panels from data', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    for (const title of [
      'Question Input',
      'Conclusion',
      'Root Cause Top-K',
      'Adtributor Candidates',
      'Evidence Table',
      'SQL Audit Table',
      'Trace Timeline',
      'Token/Latency Dashboard',
      'Reflection Issues',
      'Memory Status',
      'Memory Layers',
      'Eval Summary',
    ]) {
      expect(screen.getByRole('region', { name: title })).toBeInTheDocument();
    }
    expect(screen.getAllByText('paid_ads').length).toBeGreaterThan(0);
    expect(screen.getByText('case_total')).toBeInTheDocument();
  });

  test('renders every root cause candidate with the required top-k columns', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    const panel = within(screen.getByRole('region', { name: 'Root Cause Top-K' }));

    for (const column of ['root_cause_type', 'dimension', 'element', 'verdict']) {
      expect(panel.getByRole('columnheader', { name: column })).toBeInTheDocument();
    }
    expect(panel.getByText('campaign_traffic_drop')).toBeInTheDocument();
    expect(panel.getByText('stockout')).toBeInTheDocument();
    expect(panel.getByText('paid_ads')).toBeInTheDocument();
    expect(panel.getByText('electronics')).toBeInTheDocument();
    expect(panel.getByText('likely')).toBeInTheDocument();
  });

  test('renders adtributor candidates ordered by explanatory power', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    const panel = within(screen.getByRole('region', { name: 'Adtributor Candidates' }));
    const rows = panel.getAllByRole('row').map((row) => row.textContent ?? '');

    expect(panel.getByRole('columnheader', { name: 'explanatory_power' })).toBeInTheDocument();
    expect(panel.getByRole('columnheader', { name: 'surprise_js' })).toBeInTheDocument();
    expect(rows[1]).toContain('organic');
    expect(rows[2]).toContain('paid_ads');
  });

  test('renders token latency and layered memory observability', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    const tokenPanel = within(screen.getByRole('region', { name: 'Token/Latency Dashboard' }));
    expect(tokenPanel.getByText('total_tokens')).toBeInTheDocument();
    expect(tokenPanel.getByText('12')).toBeInTheDocument();
    expect(tokenPanel.getAllByText('latency_ms')).toHaveLength(2);
    expect(tokenPanel.getAllByText('150')).toHaveLength(2);
    expect(tokenPanel.getByText('llm_call')).toBeInTheDocument();
    expect(tokenPanel.getByText('agent_llm')).toBeInTheDocument();

    const memoryPanel = within(screen.getByRole('region', { name: 'Memory Layers' }));
    expect(memoryPanel.getByText('semantic')).toBeInTheDocument();
    expect(memoryPanel.getByText('episodic')).toBeInTheDocument();
    expect(memoryPanel.getByText('reflection')).toBeInTheDocument();
  });

  test('renders reflection_verify output summary details', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    const panel = within(screen.getByRole('region', { name: 'Reflection Issues' }));

    expect(panel.getByRole('columnheader', { name: 'seq' })).toBeInTheDocument();
    expect(panel.getByRole('columnheader', { name: 'error_code' })).toBeInTheDocument();
    expect(panel.getByRole('columnheader', { name: 'output_summary' })).toBeInTheDocument();
    expect(panel.getByText(/evidence_coverage/)).toBeInTheDocument();
    expect(panel.getByText(/candidate_missing_evidence/)).toBeInTheDocument();
  });

  test('uses injected API client for normal run workflow', async () => {
    const client = fakeClient();
    render(<App apiClient={client} />);

    await userEvent.clear(screen.getByLabelText('Question'));
    await userEvent.type(screen.getByLabelText('Question'), 'Why did GMV drop?');
    await userEvent.click(screen.getByRole('button', { name: 'Run RCA' }));

    expect(client.calls).toEqual([
      'createRun:Why did GMV drop?',
      'getRun:run-1',
      'getTrace:run-1',
      'getEvidence:run-1',
      'getSqlAudit:run-1',
      'getTasks:run-1',
      'getMemory:run-1',
    ]);
    expect((await screen.findAllByText('paid_ads')).length).toBeGreaterThan(0);
  });

  test('does not load success panels when artifact fetch fails with typed API error', async () => {
    const client = fakeClient({
      getRunError: new Error('RUN_NOT_FOUND:run not found'),
    });
    render(<App apiClient={client} />);

    await userEvent.click(screen.getByRole('button', { name: 'Run RCA' }));

    expect(await screen.findByText('RUN_NOT_FOUND:run not found')).toBeInTheDocument();
    expect(screen.getByLabelText('run status')).toHaveTextContent('failed');
    expect(screen.getByRole('region', { name: 'Root Cause Top-K' })).toHaveTextContent(
      'No persisted rows.',
    );
  });

  test('runs eval only from the eval panel control', async () => {
    const client = fakeClient();
    render(<App apiClient={client} />);

    await userEvent.click(screen.getByRole('button', { name: 'Run Eval' }));

    expect(client.calls).toEqual(['runEval']);
    expect(await screen.findByText('case_total')).toBeInTheDocument();
  });
});

function fakeClient(options: { getRunError?: Error } = {}): MetricRcaApiClient & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async createRun(payload: RunPayload) {
      calls.push(`createRun:${payload.question}`);
      return loadedRun().run;
    },
    async getRun(runId: string) {
      calls.push(`getRun:${runId}`);
      if (options.getRunError) {
        throw options.getRunError;
      }
      return loadedRun().run;
    },
    async getTrace(runId: string) {
      calls.push(`getTrace:${runId}`);
      return { run_id: runId, trace: loadedRun().trace };
    },
    async getEvidence(runId: string) {
      calls.push(`getEvidence:${runId}`);
      return { run_id: runId, evidence: loadedRun().evidence };
    },
    async getSqlAudit(runId: string) {
      calls.push(`getSqlAudit:${runId}`);
      return { run_id: runId, sql_audit: loadedRun().sqlAudit };
    },
    async getTasks(runId: string) {
      calls.push(`getTasks:${runId}`);
      return { run_id: runId, tasks: loadedRun().tasks };
    },
    async getMemory(runId: string) {
      calls.push(`getMemory:${runId}`);
      return { run_id: runId, memory: loadedRun().memory };
    },
    async runEval() {
      calls.push('runEval');
      return { eval_id: 'eval-1', summary: { case_total: 5, dangerous_sql_blocked: true }, cases: [] };
    },
  };
}

function loadedRun() {
  return {
    run: {
      run_id: 'run-1',
      status: 'succeeded',
      error_code: null,
      report: { status: 'succeeded' },
      candidates: [
        {
          root_cause_type: 'campaign_traffic_drop',
          dimension: 'channel',
          element: 'paid_ads',
          verdict: 'confirmed',
          explanatory_power: 0.91,
          surprise_js: 0.12,
        },
        {
          root_cause_type: 'stockout',
          dimension: 'category',
          element: 'electronics',
          verdict: 'likely',
          explanatory_power: 0.7,
          surprise_js: 0.08,
        },
      ],
      tasks: [{ task_id: 'task-1', title: 'Fix channel' }],
      token_summary: {
        prompt_tokens: 8,
        completion_tokens: 4,
        total_tokens: 12,
        latency_ms: 150,
        by_step: [
          { seq: 1, node: 'parse_question', action: 'parse_question', latency_ms: 0 },
          {
            seq: 2,
            node: 'llm_call',
            action: 'agent_llm',
            latency_ms: 150,
            token_usage: { prompt_tokens: 8, completion_tokens: 4, total_tokens: 12 },
          },
        ],
      },
    },
    trace: [
      { step_id: 's1', seq: 1, node: 'parse_question', action: 'parse_question' },
      {
        step_id: 's-llm',
        seq: 2,
        node: 'llm_call',
        action: 'agent_llm',
        latency_ms: 150,
        token_usage: { prompt_tokens: 8, completion_tokens: 4, total_tokens: 12 },
      },
      {
        step_id: 's2',
        seq: 3,
        node: 'reflection_verify',
        action: 'reflection_verify',
        error_code: 'ATTRIBUTION_COVERAGE_LOW',
        output_summary: {
          passed: false,
          issue_count: 1,
          issues: [{ check: 'evidence_coverage', message: 'candidate_missing_evidence' }],
          repair_count: 0,
        },
      },
    ],
    evidence: [
      {
        evidence_id: 'run-1:E4',
        guard_status: 'passed',
        sql_hash: 'abc',
        result_summary: {
          ranker: 'adtributor_internal',
          candidates: [
            {
              root_cause_type: 'campaign_traffic_drop',
              dimension: 'channel',
              element: 'paid_ads',
              verdict: 'confirmed',
              explanatory_power: 0.91,
              surprise_js: 0.12,
            },
            {
              root_cause_type: 'campaign_traffic_drop',
              dimension: 'channel',
              element: 'organic',
              verdict: 'likely',
              explanatory_power: 0.95,
              surprise_js: 0.18,
            },
          ],
        },
      },
    ],
    sqlAudit: [{ audit_id: 1, guard_status: 'passed', row_count: 1 }],
    tasks: [{ task_id: 'task-1', title: 'Fix channel' }],
    memory: [
      { memory_id: 'm-sem', layer: 'semantic', mem_key: 'gmv|semantic' },
      { memory_id: 'm-epi', layer: 'episodic', mem_key: 'gmv|channel' },
      { memory_id: 'm-ref', layer: 'reflection', mem_key: 'run-1|reflection' },
    ],
    evalStatus: {
      summary: { case_total: 5, dangerous_sql_blocked: true },
    },
  };
}
