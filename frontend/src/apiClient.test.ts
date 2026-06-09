import { afterEach, describe, expect, test, vi } from 'vitest';
import { HttpMetricRcaApiClient, isApiError } from './apiClient';

describe('HttpMetricRcaApiClient', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('constructing client does not make network calls', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    new HttpMetricRcaApiClient('http://127.0.0.1:8000');

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test('uses browser fetch for API calls', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: 'run-1', status: 'succeeded' }),
    } as Response);
    const client = new HttpMetricRcaApiClient('http://127.0.0.1:8000/');

    await client.getRun('run-1');

    expect(fetchSpy).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/rca/runs/run-1',
      expect.objectContaining({ headers: { 'content-type': 'application/json' } }),
    );
  });

  test('recognizes typed API errors without faking success', () => {
    expect(
      isApiError({
        error_code: 'SYSTEM_TABLE_READ_FAILED',
        message: 'system table read failed',
        recoverable: false,
        retryable: false,
        trace_step_id: null,
        suggested_next_action: null,
      }),
    ).toBe(true);
  });
});
