export type TraceStep = {
  step_id: string;
  run_id: string;
  seq: number;
  node: string;
  action: string;
  input_summary: Record<string, any> | null;
  output_summary: Record<string, any> | null;
  error_code: string | null;
  latency_ms: number;
  created_at: string;
};

export type Evidence = {
  evidence_id: string;
  guard_status: "passed" | "rejected" | "warning";
  sql_hash: string;
  sql_text: string;
  query_spec: Record<string, any>;
  result_summary: Record<string, any>;
  data_source: string;
  created_at: string;
  produced_by_step?: string;
};

export type SqlAudit = {
  audit_id: string;
  guard_status: "passed" | "rejected";
  sql_hash: string;
  sql_text: string;
  row_count: number;
  latency_ms: number;
  guard_errors: string[];
  created_at: string;
};

export type Candidate = {
  candidate_id: string;
  root_cause_type: string;
  dimension: string;
  element: string;
  verdict: "confirmed" | "likely" | "insufficient" | "ruled_out";
  contribution_pct?: number;
  eng_confidence?: number;
  evidence_ids: string[];
  rationale?: string;
};

export type NumericClaim = { name: string; value: number | string; unit?: string; evidence_id: string };

export type RunResponse = {
  run_id: string;
  status: "succeeded" | "no_anomaly" | "failed" | "running";
  metric_id: string;
  target_date: string;
  business_today: string;
  question: string;
  created_at: string;
  error?: {
    error_code: string;
    message: string;
    recoverable: boolean;
    retryable: boolean;
    trace_step_id?: string;
    suggested_next_action?: string;
  };
  report?: {
    top_candidate?: Candidate;
    numeric_claims: NumericClaim[];
    narrative: string;
  };
  candidates?: Candidate[];
  links: Record<string, string>;
};

export type MemoryRec = {
  step_id: string;
  node: string;
  output_summary: Record<string, any>;
  error_code: string | null;
  created_at: string;
};

export type TaskRec = {
  task_id: string;
  title: string;
  root_cause_type: string;
  payload: Record<string, any>;
  created_at: string;
};

export type EvalSummary = {
  eval_id: string;
  case_total: number;
  top1_rate: number;
  top3_rate: number;
  anomaly_accuracy: number;
  evidence_coverage_avg: number;
  sql_safe_rate: number;
  report_traceable_rate: number;
  reflection_repair_ok: boolean;
  memory_pollution_ok: boolean;
  dangerous_sql_blocked: boolean;
  no_anomaly_correct: boolean;
  thresholds_met: boolean;
  cases: Array<{ case_id: string; metric_id: string; expected: string; actual: string; passed: boolean }>;
};

const RID = "run_2026_06_09_a13f";

export const MOCK_RUN: RunResponse = {
  run_id: RID,
  status: "succeeded",
  metric_id: "gmv_daily_cn",
  target_date: "2026-06-08",
  business_today: "2026-06-09",
  question: "为什么 2026-06-08 全站 GMV 环比下跌 12.4%?定位主因维度。",
  created_at: "2026-06-09T03:14:02Z",
  report: {
    narrative:
      "2026-06-08 全站 GMV 环比下跌 12.4%。主因为 channel=paid_search 渠道贡献度 −68.1%,叠加 region=华东 SKU 缺货放大效应。建议立即排查 paid_search 投放预算异动。",
    numeric_claims: [
      { name: "gmv_drop_pct", value: -12.4, unit: "%", evidence_id: "ev_002" },
      { name: "paid_search_contribution", value: -68.1, unit: "%", evidence_id: "ev_004" },
      { name: "huadong_stockout_rate", value: 18.7, unit: "%", evidence_id: "ev_005" },
      { name: "paid_search_spend_delta", value: -42.3, unit: "%", evidence_id: "ev_006" },
    ],
    top_candidate: {
      candidate_id: "c_001",
      root_cause_type: "channel_anomaly",
      dimension: "channel",
      element: "paid_search",
      verdict: "confirmed",
      contribution_pct: 68.1,
      eng_confidence: 0.92,
      evidence_ids: ["ev_004", "ev_006"],
      rationale: "paid_search 投放预算环比 −42.3%,直接导致渠道 GMV 同步下跌。",
    },
  },
  candidates: [
    {
      candidate_id: "c_001",
      root_cause_type: "channel_anomaly",
      dimension: "channel",
      element: "paid_search",
      verdict: "confirmed",
      contribution_pct: 68.1,
      eng_confidence: 0.92,
      evidence_ids: ["ev_004", "ev_006"],
    },
    {
      candidate_id: "c_002",
      root_cause_type: "supply_anomaly",
      dimension: "region",
      element: "huadong",
      verdict: "likely",
      contribution_pct: 21.4,
      eng_confidence: 0.74,
      evidence_ids: ["ev_005"],
    },
    {
      candidate_id: "c_003",
      root_cause_type: "category_anomaly",
      dimension: "category",
      element: "consumer_electronics",
      verdict: "insufficient",
      contribution_pct: 6.2,
      eng_confidence: 0.31,
      evidence_ids: ["ev_007"],
    },
    {
      candidate_id: "c_004",
      root_cause_type: "pricing_anomaly",
      dimension: "promo",
      element: "summer_sale_2025",
      verdict: "ruled_out",
      contribution_pct: 0.8,
      eng_confidence: 0.12,
      evidence_ids: [],
    },
  ],
  links: {
    self: `/api/rca/runs/${RID}`,
    trace: `/api/rca/runs/${RID}/trace`,
    evidence: `/api/rca/runs/${RID}/evidence`,
    sql_audit: `/api/rca/runs/${RID}/sql-audit`,
    tasks: `/api/rca/runs/${RID}/tasks`,
    memory: `/api/rca/runs/${RID}/memory`,
  },
};

const t0 = new Date("2026-06-09T03:14:02Z").getTime();
const ts = (offset: number) => new Date(t0 + offset).toISOString();

export const MOCK_TRACE: TraceStep[] = [
  {
    step_id: "s_01", run_id: RID, seq: 1, node: "parse_question", action: "parse",
    input_summary: { question: MOCK_RUN.question, target_date: "2026-06-08" },
    output_summary: { metric_id: "gmv_daily_cn", intent: "diagnose_drop", target_date: "2026-06-08" },
    error_code: null, latency_ms: 142, created_at: ts(0),
  },
  {
    step_id: "s_02", run_id: RID, seq: 2, node: "read_memory", action: "lookup",
    input_summary: { metric_id: "gmv_daily_cn" },
    output_summary: { hits: 2, hint: "paid_search has produced anomalies on similar drops 3x in past 30d" },
    error_code: null, latency_ms: 38, created_at: ts(200),
  },
  {
    step_id: "s_03", run_id: RID, seq: 3, node: "plan_init", action: "plan",
    input_summary: { intent: "diagnose_drop", memory_hint: true },
    output_summary: { plan: ["detect_anomaly", "drilldown:channel", "drilldown:region", "fetch_signals", "attribute"] },
    error_code: null, latency_ms: 412, created_at: ts(450),
  },
  {
    step_id: "s_04", run_id: RID, seq: 4, node: "react_step", action: "act:detect_anomaly",
    input_summary: { metric_id: "gmv_daily_cn", date: "2026-06-08" },
    output_summary: { tool_call: "detect_anomaly", args: { window_days: 28 } },
    error_code: null, latency_ms: 220, created_at: ts(900),
  },
  {
    step_id: "s_05", run_id: RID, seq: 5, node: "execute_tool", action: "detect_anomaly",
    input_summary: { metric_id: "gmv_daily_cn", window_days: 28 },
    output_summary: { is_anomaly: true, severity: "high", drop_pct: -12.4, evidence_id: "ev_001" },
    error_code: null, latency_ms: 1842, created_at: ts(1150),
  },
  {
    step_id: "s_06", run_id: RID, seq: 6, node: "react_step", action: "act:drilldown_dimension",
    input_summary: { dimension: "channel" },
    output_summary: { tool_call: "drilldown_dimension", args: { dim: "channel" } },
    error_code: null, latency_ms: 198, created_at: ts(3010),
  },
  {
    step_id: "s_07", run_id: RID, seq: 7, node: "execute_tool", action: "drilldown_dimension",
    input_summary: { dim: "channel" },
    output_summary: { top_offenders: [{ element: "paid_search", delta_pct: -42.3 }, { element: "organic", delta_pct: -3.1 }], evidence_id: "ev_002" },
    error_code: null, latency_ms: 2204, created_at: ts(3220),
  },
  {
    step_id: "s_08", run_id: RID, seq: 8, node: "react_step", action: "act:drilldown_dimension",
    input_summary: { dimension: "region" },
    output_summary: { tool_call: "drilldown_dimension", args: { dim: "region" } },
    error_code: null, latency_ms: 175, created_at: ts(5450),
  },
  {
    step_id: "s_09", run_id: RID, seq: 9, node: "execute_tool", action: "drilldown_dimension",
    input_summary: { dim: "region" },
    output_summary: { top_offenders: [{ element: "huadong", delta_pct: -19.8 }], evidence_id: "ev_003" },
    error_code: null, latency_ms: 1611, created_at: ts(5640),
  },
  {
    step_id: "s_10", run_id: RID, seq: 10, node: "react_step", action: "act:fetch_related_signal",
    input_summary: { dim: "channel", element: "paid_search" },
    output_summary: { tool_call: "fetch_related_signal", args: { signal: "ad_spend" } },
    error_code: null, latency_ms: 184, created_at: ts(7270),
  },
  {
    step_id: "s_11", run_id: RID, seq: 11, node: "execute_tool", action: "fetch_related_signal",
    input_summary: { signal: "ad_spend", channel: "paid_search" },
    output_summary: { spend_delta_pct: -42.3, confidence: 0.94, evidence_id: "ev_006" },
    error_code: null, latency_ms: 1320, created_at: ts(7470),
  },
  {
    step_id: "s_12", run_id: RID, seq: 12, node: "attribute_rank", action: "rank",
    input_summary: { candidates: 4 },
    output_summary: { ordered: ["c_001", "c_002", "c_003", "c_004"], method: "shapley_lite" },
    error_code: null, latency_ms: 612, created_at: ts(8810),
  },
  {
    step_id: "s_13", run_id: RID, seq: 13, node: "reflection_verify", action: "verify",
    input_summary: { candidates: 4, numeric_claims: 4 },
    output_summary: {
      passed: true,
      repaired: true,
      repair_count: 1,
      issues: [
        { check: "numeric_claim.has_evidence", severity: "warning", by: "rule", message: "claim huadong_stockout_rate originally missing evidence_id", suggested_action: "re-bind to ev_005" },
      ],
    },
    error_code: null, latency_ms: 942, created_at: ts(9430),
  },
  {
    step_id: "s_14", run_id: RID, seq: 14, node: "generate_report", action: "render",
    input_summary: { top_candidate: "c_001", claims: 4 },
    output_summary: { report_id: "rep_001", word_count: 318 },
    error_code: null, latency_ms: 1180, created_at: ts(10380),
  },
  {
    step_id: "s_15", run_id: RID, seq: 15, node: "create_tasks", action: "create",
    input_summary: { top_candidate: "c_001" },
    output_summary: { task_ids: ["t_001", "t_002"], count: 2 },
    error_code: null, latency_ms: 410, created_at: ts(11570),
  },
  {
    step_id: "s_16", run_id: RID, seq: 16, node: "write_memory", action: "persist",
    input_summary: { run_id: RID, verdict: "confirmed" },
    output_summary: { written: true, keys: ["channel_anomaly:paid_search"] },
    error_code: null, latency_ms: 84, created_at: ts(11990),
  },
];

export const MOCK_EVIDENCE: Evidence[] = [
  {
    evidence_id: "ev_001",
    guard_status: "passed",
    sql_hash: "9f3a:b71c:e02d",
    sql_text:
      "SELECT date, SUM(gmv) AS gmv\nFROM fact_orders\nWHERE date BETWEEN '2026-05-10' AND '2026-06-08'\n  AND country = 'CN'\nGROUP BY date\nORDER BY date;",
    query_spec: { metric_id: "gmv_daily_cn", time_range: ["2026-05-10", "2026-06-08"], group_by: ["date"], filters: { country: "CN" }, limit: 1000, purpose: "detect_anomaly" },
    result_summary: { rows: 30, latest_value: 9842113.5, prev_value: 11235004.1, drop_pct: -12.4 },
    data_source: "warehouse.fact_orders",
    created_at: ts(2200),
    produced_by_step: "s_05",
  },
  {
    evidence_id: "ev_002",
    guard_status: "passed",
    sql_hash: "1a8c:42de:7790",
    sql_text:
      "SELECT channel, SUM(gmv) AS gmv, SUM(gmv) / SUM(SUM(gmv)) OVER () AS share\nFROM fact_orders\nWHERE date = '2026-06-08' AND country = 'CN'\nGROUP BY channel\nORDER BY gmv DESC;",
    query_spec: { metric_id: "gmv_daily_cn", time_range: ["2026-06-08", "2026-06-08"], group_by: ["channel"], filters: { country: "CN" }, limit: 100, purpose: "drilldown_channel" },
    result_summary: { rows: 6, top: { channel: "paid_search", delta_pct: -42.3 } },
    data_source: "warehouse.fact_orders",
    created_at: ts(4900),
    produced_by_step: "s_07",
  },
  {
    evidence_id: "ev_003",
    guard_status: "passed",
    sql_hash: "c2d1:99af:01bb",
    sql_text:
      "SELECT region, SUM(gmv) AS gmv\nFROM fact_orders\nWHERE date = '2026-06-08'\nGROUP BY region;",
    query_spec: { metric_id: "gmv_daily_cn", group_by: ["region"], filters: {}, limit: 50, purpose: "drilldown_region" },
    result_summary: { rows: 7, top: { region: "huadong", delta_pct: -19.8 } },
    data_source: "warehouse.fact_orders",
    created_at: ts(6900),
    produced_by_step: "s_09",
  },
  {
    evidence_id: "ev_004",
    guard_status: "passed",
    sql_hash: "70bb:cd4a:5512",
    sql_text:
      "SELECT channel, contribution_pct\nFROM v_attribution_shapley\nWHERE run_id = 'run_2026_06_09_a13f';",
    query_spec: { metric_id: "gmv_daily_cn", group_by: ["channel"], purpose: "attribution" },
    result_summary: { rows: 6, paid_search_contribution_pct: 68.1 },
    data_source: "analytics.v_attribution_shapley",
    created_at: ts(9100),
    produced_by_step: "s_12",
  },
  {
    evidence_id: "ev_005",
    guard_status: "passed",
    sql_hash: "ff20:7e4c:00aa",
    sql_text:
      "SELECT region, AVG(is_oos)::float AS stockout_rate\nFROM fact_inventory\nWHERE date = '2026-06-08'\nGROUP BY region;",
    query_spec: { metric_id: "stockout_rate", group_by: ["region"], purpose: "signal_supply" },
    result_summary: { rows: 7, huadong_stockout_rate: 0.187 },
    data_source: "warehouse.fact_inventory",
    created_at: ts(7100),
  },
  {
    evidence_id: "ev_006",
    guard_status: "passed",
    sql_hash: "3df8:9c20:b811",
    sql_text:
      "SELECT channel, SUM(spend) AS spend\nFROM fact_ad_spend\nWHERE date BETWEEN '2026-06-01' AND '2026-06-08'\nGROUP BY channel;",
    query_spec: { metric_id: "ad_spend", group_by: ["channel"], purpose: "signal_marketing" },
    result_summary: { rows: 4, paid_search_delta_pct: -42.3 },
    data_source: "marketing.fact_ad_spend",
    created_at: ts(8500),
    produced_by_step: "s_11",
  },
  {
    evidence_id: "ev_007",
    guard_status: "rejected",
    sql_hash: "00cf:31aa:dead",
    sql_text:
      "SELECT * FROM fact_orders WHERE country = 'CN';  -- rejected: no time bound, no limit",
    query_spec: { metric_id: "gmv_daily_cn", purpose: "drilldown_category" },
    result_summary: {},
    data_source: "warehouse.fact_orders",
    created_at: ts(6100),
  },
];

export const MOCK_SQL_AUDIT: SqlAudit[] = [
  { audit_id: "a_01", guard_status: "passed", sql_hash: "9f3a:b71c:e02d", sql_text: MOCK_EVIDENCE[0].sql_text, row_count: 30, latency_ms: 1842, guard_errors: [], created_at: ts(2200) },
  { audit_id: "a_02", guard_status: "passed", sql_hash: "1a8c:42de:7790", sql_text: MOCK_EVIDENCE[1].sql_text, row_count: 6, latency_ms: 2204, guard_errors: [], created_at: ts(4900) },
  { audit_id: "a_03", guard_status: "passed", sql_hash: "c2d1:99af:01bb", sql_text: MOCK_EVIDENCE[2].sql_text, row_count: 7, latency_ms: 1611, guard_errors: [], created_at: ts(6900) },
  {
    audit_id: "a_04", guard_status: "rejected", sql_hash: "00cf:31aa:dead",
    sql_text: "SELECT * FROM fact_orders WHERE country = 'CN';",
    row_count: 0, latency_ms: 12, guard_errors: ["MISSING_TIME_RANGE", "MISSING_LIMIT", "SELECT_STAR_DISALLOWED"], created_at: ts(6100),
  },
  { audit_id: "a_05", guard_status: "passed", sql_hash: "3df8:9c20:b811", sql_text: MOCK_EVIDENCE[5].sql_text, row_count: 4, latency_ms: 1320, guard_errors: [], created_at: ts(8500) },
  { audit_id: "a_06", guard_status: "passed", sql_hash: "70bb:cd4a:5512", sql_text: MOCK_EVIDENCE[3].sql_text, row_count: 6, latency_ms: 612, guard_errors: [], created_at: ts(9100) },
];

export const MOCK_MEMORY: MemoryRec[] = [
  { step_id: "s_02", node: "read_memory", output_summary: { hits: 2, top_hint: "paid_search:high_recurrence", priority_boost: 0.3 }, error_code: null, created_at: ts(200) },
  { step_id: "s_16", node: "write_memory", output_summary: { written: true, keys: ["channel_anomaly:paid_search", "gmv_daily_cn:2026-06-08"] }, error_code: null, created_at: ts(11990) },
];

export const MOCK_TASKS: TaskRec[] = [
  { task_id: "t_001", title: "排查 paid_search 投放预算下调原因", root_cause_type: "channel_anomaly", payload: { owner: "marketing-ops", channel: "paid_search", delta_pct: -42.3, priority: "P0" }, created_at: ts(11600) },
  { task_id: "t_002", title: "华东仓 SKU 缺货补货评估", root_cause_type: "supply_anomaly", payload: { owner: "supply-chain", region: "huadong", stockout_rate: 0.187, priority: "P1" }, created_at: ts(11700) },
];

export const MOCK_EVAL: EvalSummary = {
  eval_id: "eval_2026_06_w23",
  case_total: 48,
  top1_rate: 0.812,
  top3_rate: 0.937,
  anomaly_accuracy: 0.958,
  evidence_coverage_avg: 0.91,
  sql_safe_rate: 1.0,
  report_traceable_rate: 0.978,
  reflection_repair_ok: true,
  memory_pollution_ok: true,
  dangerous_sql_blocked: true,
  no_anomaly_correct: true,
  thresholds_met: true,
  cases: [
    { case_id: "case_01", metric_id: "gmv_daily_cn", expected: "channel:paid_search", actual: "channel:paid_search", passed: true },
    { case_id: "case_02", metric_id: "dau_app", expected: "region:huabei", actual: "region:huabei", passed: true },
    { case_id: "case_03", metric_id: "conv_rate", expected: "no_anomaly", actual: "no_anomaly", passed: true },
    { case_id: "case_04", metric_id: "aov_daily", expected: "category:fashion", actual: "category:beauty", passed: false },
  ],
};

/* Alt runs for status demonstration */
export const RUNS_INDEX: Array<{ run_id: string; status: RunResponse["status"]; metric_id: string; target_date: string }> = [
  { run_id: RID, status: "succeeded", metric_id: "gmv_daily_cn", target_date: "2026-06-08" },
  { run_id: "run_2026_06_08_71ab", status: "no_anomaly", metric_id: "conv_rate", target_date: "2026-06-07" },
  { run_id: "run_2026_06_07_44ef", status: "failed", metric_id: "dau_app", target_date: "2026-06-06" },
];

export const MOCK_RUN_NO_ANOMALY: RunResponse = {
  run_id: "run_2026_06_08_71ab",
  status: "no_anomaly",
  metric_id: "conv_rate",
  target_date: "2026-06-07",
  business_today: "2026-06-09",
  question: "2026-06-07 转化率是否异常?",
  created_at: "2026-06-08T02:00:00Z",
  report: { narrative: "未检测到显著异常,指标位于 28 日波动带内。", numeric_claims: [], top_candidate: undefined },
  candidates: [],
  links: { self: "/api/rca/runs/run_2026_06_08_71ab", trace: "...", evidence: "...", sql_audit: "...", tasks: "...", memory: "..." },
};

export const MOCK_RUN_FAILED: RunResponse = {
  run_id: "run_2026_06_07_44ef",
  status: "failed",
  metric_id: "dau_app",
  target_date: "2026-06-06",
  business_today: "2026-06-09",
  question: "2026-06-06 DAU 异常归因",
  created_at: "2026-06-07T02:14:00Z",
  error: {
    error_code: "SQL_GUARD_REJECTED",
    message: "Plan attempted SELECT * without time bound on fact_events; blocked by SQL guard.",
    recoverable: true,
    retryable: true,
    trace_step_id: "s_07_failed",
    suggested_next_action: "Re-plan with bounded time range (≤ 30 days) and explicit column projection.",
  },
  links: { self: "/api/rca/runs/run_2026_06_07_44ef", trace: "...", evidence: "...", sql_audit: "...", tasks: "...", memory: "..." },
};
