import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';
import { App } from './App';
import type { MetricRcaApiClient, RunPayload } from './apiClient';

describe('MetricRCA UI', () => {
  afterEach(() => cleanup());

  test('renders nine required persisted-artifact panels from data', () => {
    render(<App apiClient={fakeClient()} initialData={loadedRun()} />);

    for (const title of [
      'Question Input',
      'Conclusion',
      'Root Cause Top-K',
      'Evidence Table',
      'SQL Audit Table',
      'Trace Timeline',
      'Reflection Issues',
      'Memory Status',
      'Eval Summary',
    ]) {
      expect(screen.getByRole('region', { name: title })).toBeInTheDocument();
    }
    expect(screen.getByText('paid_ads')).toBeInTheDocument();
    expect(screen.getByText('case_total')).toBeInTheDocument();
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
    expect(await screen.findByText('paid_ads')).toBeInTheDocument();
  });

  test('runs eval only from the eval panel control', async () => {
    const client = fakeClient();
    render(<App apiClient={client} />);

    await userEvent.click(screen.getByRole('button', { name: 'Run Eval' }));

    expect(client.calls).toEqual(['runEval']);
    expect(await screen.findByText('case_total')).toBeInTheDocument();
  });
});

function fakeClient(): MetricRcaApiClient & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async createRun(payload: RunPayload) {
      calls.push(`createRun:${payload.question}`);
      return loadedRun().run;
    },
    async getRun(runId: string) {
      calls.push(`getRun:${runId}`);
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
        },
      ],
      tasks: [{ task_id: 'task-1', title: 'Fix channel' }],
    },
    trace: [
      { step_id: 's1', seq: 1, node: 'parse_question', action: 'parse_question' },
      { step_id: 's2', seq: 2, node: 'reflection_verify', action: 'reflection_verify' },
    ],
    evidence: [{ evidence_id: 'run-1:E4', guard_status: 'passed', sql_hash: 'abc' }],
    sqlAudit: [{ audit_id: 1, guard_status: 'passed', row_count: 1 }],
    tasks: [{ task_id: 'task-1', title: 'Fix channel' }],
    memory: [{ step_id: 'm1', node: 'read_memory' }],
    evalStatus: {
      summary: { case_total: 5, dangerous_sql_blocked: true },
    },
  };
}
