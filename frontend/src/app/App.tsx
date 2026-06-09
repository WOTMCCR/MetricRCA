import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, FolderTree, FileCheck, ShieldCheck, ScanSearch, ListTodo,
  RefreshCw, X, AlertTriangle, RotateCcw, Search, Copy, Check,
} from "lucide-react";
import { HttpMetricRcaApiClient, type MetricRcaApiClient, type RunPayload } from "../apiClient";
import type { RunResponse } from "./components/mockData";
import { NodeGraph, type GraphSelection } from "./components/NodeGraph";
import {
  VerdictBar, CandidatesView, EvidenceView, SQLAuditView, ReflectionView, TasksView,
  StepDrawer, LoopDrawer, ConclusionCard,
} from "./components/Views";
import { StatusPill, Card, CopyableId } from "./components/ui-bits";
import {
  buildLoadingRun,
  toInvestigationBundle,
  type InvestigationBundle,
  type RunRequestContext,
} from "./apiAdapter";

type ViewKey = "investigation" | "candidates" | "evidence" | "sql_audit" | "reflection" | "tasks";

const NAV: Array<{ key: ViewKey; label: string; icon: any }> = [
  { key: "investigation", label: "Investigation", icon: Activity },
  { key: "candidates", label: "Candidates", icon: FolderTree },
  { key: "evidence", label: "Evidence", icon: FileCheck },
  { key: "sql_audit", label: "SQL Audit", icon: ShieldCheck },
  { key: "reflection", label: "Reflection", icon: ScanSearch },
  { key: "tasks", label: "Tasks", icon: ListTodo },
];

const DEFAULT_QUESTION = "Why did yesterday GMV drop?";
const defaultClient = new HttpMetricRcaApiClient();

function shortId(id: string) {
  if (id.length <= 16) return id;
  return `${id.slice(0, 8)}...${id.slice(-6)}`;
}

export default function App({
  apiClient = defaultClient,
  initialData,
}: {
  apiClient?: MetricRcaApiClient;
  initialData?: InvestigationBundle;
}) {
  const [current, setCurrent] = useState<InvestigationBundle | null>(initialData ?? null);
  const [runHistory, setRunHistory] = useState<InvestigationBundle[]>(initialData ? [initialData] : []);
  const [view, setView] = useState<ViewKey>("investigation");
  const [selection, setSelection] = useState<GraphSelection>(null);
  const [highlightEvidence, setHighlightEvidence] = useState<string | null>(null);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRunSwitching, setIsRunSwitching] = useState(false);

  const run = current?.run ?? buildLoadingRun(DEFAULT_QUESTION);
  const trace = current?.trace ?? [];

  useEffect(() => {
    if (!initialData) {
      void dispatchRun({ question: DEFAULT_QUESTION, memory_enabled: false });
    }
    // Intentionally run once. Re-run actions are explicit user events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runIndex = useMemo(
    () =>
      runHistory.map((item) => ({
        run_id: item.run.run_id,
        status: item.run.status,
        metric_id: item.run.metric_id,
        target_date: item.run.target_date,
      })),
    [runHistory],
  );

  async function dispatchRun(payload: RunPayload) {
    const context: RunRequestContext = {
      question: payload.question,
      targetDate: payload.target_date,
      businessToday: payload.business_today,
    };
    setCurrent({
      run: buildLoadingRun(payload.question),
      trace: [],
      evidence: [],
      sqlAudit: [],
      memory: [],
      tasks: [],
    });
    setError(null);
    setView("investigation");
    setSelection(null);
    setHighlightEvidence(null);
    try {
      const created = await apiClient.createRun(payload);
      const [latestRun, traceRes, evidenceRes, sqlAuditRes, tasksRes] = await Promise.all([
        apiClient.getRun(created.run_id),
        apiClient.getTrace(created.run_id),
        apiClient.getEvidence(created.run_id),
        apiClient.getSqlAudit(created.run_id),
        apiClient.getTasks(created.run_id),
      ]);
      const loaded = toInvestigationBundle(
        latestRun,
        traceRes,
        evidenceRes,
        sqlAuditRes,
        tasksRes,
        { run_id: created.run_id, memory: [] },
        context,
      );
      setCurrent(loaded);
      setRunHistory((items) => [loaded, ...items.filter((item) => item.run.run_id !== loaded.run.run_id)].slice(0, 12));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "UI_REQUEST_FAILED");
      setCurrent({
        run: {
          ...buildLoadingRun(payload.question),
          run_id: "failed",
          status: "failed",
          error: {
            error_code: "UI_REQUEST_FAILED",
            message: exc instanceof Error ? exc.message : "UI_REQUEST_FAILED",
            recoverable: true,
            retryable: true,
          },
        },
        trace: [],
        evidence: [],
        sqlAudit: [],
        memory: [],
        tasks: [],
      });
    }
  }

  async function loadRunById(runId: string) {
    if (!runId || runId === run.run_id) return;
    const cached = runHistory.find((item) => item.run.run_id === runId);
    const context: RunRequestContext = {
      question: cached?.run.question ?? current?.run.question ?? DEFAULT_QUESTION,
      targetDate: cached?.run.target_date ?? current?.run.target_date,
      businessToday: cached?.run.business_today ?? current?.run.business_today,
    };
    setIsRunSwitching(true);
    setError(null);
    setSelection(null);
    setHighlightEvidence(null);
    try {
      const [latestRun, traceRes, evidenceRes, sqlAuditRes, tasksRes] = await Promise.all([
        apiClient.getRun(runId),
        apiClient.getTrace(runId),
        apiClient.getEvidence(runId),
        apiClient.getSqlAudit(runId),
        apiClient.getTasks(runId),
      ]);
      const loaded = toInvestigationBundle(
        latestRun,
        traceRes,
        evidenceRes,
        sqlAuditRes,
        tasksRes,
        { run_id: runId, memory: [] },
        context,
      );
      setCurrent(loaded);
      setRunHistory((items) => [loaded, ...items.filter((item) => item.run.run_id !== loaded.run.run_id)].slice(0, 12));
      setView("investigation");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "UI_RUN_LOAD_FAILED");
    } finally {
      setIsRunSwitching(false);
    }
  }

  const focusEvidence = (id: string) => {
    setHighlightEvidence(id);
    setView("evidence");
    setTimeout(() => {
      document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  };

  const locateStep = (stepId: string) => {
    const step = trace.find((t) => t.step_id === stepId);
    if (step) {
      setSelection({ type: "step", step });
      setView("investigation");
    }
  };

  const changeView = (next: ViewKey) => {
    setView(next);
    setSelection(null);
  };

  return (
    <div
      className="min-h-screen bg-slate-50 text-slate-900 grid overflow-hidden"
      style={{ gridTemplateRows: "auto auto auto minmax(0, 1fr)" }}
    >
      <TopHeader
        run={run}
        runId={run.run_id}
        runsIndex={runIndex}
        switching={isRunSwitching}
        onRunChange={(id) => void loadRunById(id)}
        onRerun={() => setRerunOpen(true)}
      />
      <RunMetadataBar run={run} />
      <GlobalDiagnoseBar
        running={run.status === "running"}
        onSubmit={(payload) => void dispatchRun(payload)}
      />
      <div className="min-h-0 investigation-layout">
        <SidebarNav view={view} onChange={changeView} />
        <main className="min-w-0 overflow-y-auto bg-slate-50">
          <div style={{ padding: "24px 32px" }} className="space-y-5 min-w-0">
            {error && run.status !== "failed" && (
              <div className="border border-red-200 bg-red-50 rounded-xl p-4 text-[13px] text-red-700">
                {error}
              </div>
            )}
            {run.status === "failed" && <FailedBanner run={run} />}
            {view === "investigation" && (
              <>
                <RunRecordHeading run={run} />
                <VerdictBar run={run} onClaimClick={focusEvidence} />
                <ConclusionCard run={run} evidence={current?.evidence ?? []} onEvidenceClick={focusEvidence} />
                <Card className="p-3" >
                  <div className="flex items-center justify-between px-2 py-2 mb-2 border-b border-slate-200">
                    <div className="flex items-center gap-2 min-w-0">
                      <Activity size={14} className="text-blue-600 shrink-0" />
                      <span className="text-[14px] font-semibold text-slate-900">RCA Execution Path</span>
                      <span className="text-[12px] text-slate-500 ml-1">{trace.length} trace steps</span>
                    </div>
                  </div>
                  <div className="rounded-lg overflow-hidden bg-slate-50">
                    <NodeGraph
                      trace={trace}
                      selectedStepId={selection?.type === "step" ? selection.step.step_id : null}
                      onSelect={setSelection}
                    />
                  </div>
                </Card>
              </>
            )}
            {view === "candidates" && <CandidatesView run={run} onEvidenceClick={focusEvidence} />}
            {view === "evidence" && (
              current?.evidence.length
                ? <EvidenceView items={current.evidence} highlightId={highlightEvidence} onLocateStep={locateStep} />
                : <Card className="p-8"><EmptyMsg text="This run has no evidence yet." /></Card>
            )}
            {view === "sql_audit" && <SQLAuditView items={current?.sqlAudit ?? []} />}
            {view === "reflection" && <ReflectionView trace={trace} />}
            {view === "tasks" && <TasksView items={current?.tasks ?? []} run={run} />}
          </div>
        </main>
      </div>
      <ContextDrawer
        selection={selection}
        onClose={() => setSelection(null)}
        onEvidenceClick={focusEvidence}
      />
      {rerunOpen && (
        <RerunModal
          onClose={() => setRerunOpen(false)}
          onSubmit={(payload) => {
            setRerunOpen(false);
            void dispatchRun(payload);
          }}
        />
      )}
    </div>
  );
}

function GlobalDiagnoseBar({
  running,
  onSubmit,
}: {
  running: boolean;
  onSubmit: (payload: RunPayload) => void;
}) {
  return (
    <section className="bg-white border-b border-slate-200 px-6 py-3">
      <div className="mx-auto max-w-[1280px] min-w-0">
        <div className="mb-2 flex items-center justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div className="text-[14px] font-semibold text-slate-900">New diagnosis</div>
            <div className="text-[12px] text-slate-500 mt-0.5">Start a new RCA run from a metric issue.</div>
          </div>
        </div>
        <DiagnosePanel running={running} onSubmit={onSubmit} />
      </div>
    </section>
  );
}

function RunRecordHeading({ run }: { run: RunResponse }) {
  return (
    <div className="flex items-end justify-between gap-4 flex-wrap">
      <div className="min-w-0">
        <div className="text-[18px] font-semibold text-slate-900">Current diagnosis record</div>
        <div className="mt-1 text-[13px] text-slate-500">
          Generated report, trace, evidence, SQL audit, and tasks for this selected run.
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <CopyableId id={run.run_id} />
        <StatusPill kind={run.status === "succeeded" ? "ok" : run.status === "running" ? "warn" : run.status === "failed" ? "error" : "muted"}>
          {run.status}
        </StatusPill>
      </div>
    </div>
  );
}

function EmptyMsg({ text }: { text: string }) {
  return <div className="text-center py-8 text-[13px] text-slate-500">{text}</div>;
}

function DiagnosePanel({
  running,
  onSubmit,
}: {
  running: boolean;
  onSubmit: (payload: RunPayload) => void;
}) {
  const [question, setQuestion] = useState(DEFAULT_QUESTION);
  const [targetDate, setTargetDate] = useState("2026-06-05");
  const [businessToday, setBusinessToday] = useState("2026-06-06");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const q = question.trim();
    if (!q) {
      setError("Question is required");
      return;
    }
    setError(null);
    onSubmit({
      question: q,
      target_date: targetDate.trim() || undefined,
      business_today: businessToday.trim() || undefined,
      memory_enabled: false,
    });
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(280px,1fr)_140px_150px_auto] gap-2.5 items-end">
        <div className="min-w-0">
          <label htmlFor="diagnose-question" className="block text-[12px] font-medium text-slate-500 mb-1.5">Metric issue</label>
          <input
            id="diagnose-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-slate-900 outline-none focus:border-blue-600"
            placeholder="Why did yesterday GMV drop?"
          />
        </div>
        <div>
          <label htmlFor="diagnose-target-date" className="block text-[12px] font-medium text-slate-500 mb-1.5">Target date</label>
          <input
            id="diagnose-target-date"
            value={targetDate}
            onChange={(event) => setTargetDate(event.target.value)}
            className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 mono text-[13px] text-slate-900 outline-none focus:border-blue-600"
          />
        </div>
        <div>
          <label htmlFor="diagnose-business-today" className="block text-[12px] font-medium text-slate-500 mb-1.5">Business today</label>
          <input
            id="diagnose-business-today"
            value={businessToday}
            onChange={(event) => setBusinessToday(event.target.value)}
            className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 mono text-[13px] text-slate-900 outline-none focus:border-blue-600"
          />
        </div>
        <button
          type="button"
          disabled={running}
          onClick={submit}
          className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-4 text-[13px] font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Search size={14} /> {running ? "Running..." : "Diagnose"}
        </button>
      </div>
      {error && <div className="mt-2 text-[12px] text-red-600">Warning: {error}</div>}
    </div>
  );
}

function TopHeader({
  run, runId, runsIndex, switching, onRunChange, onRerun,
}: {
  run: RunResponse;
  runId: string;
  runsIndex: Array<{ run_id: string; status: RunResponse["status"]; metric_id: string; target_date: string }>;
  switching: boolean;
  onRunChange: (id: string) => void;
  onRerun: () => void;
}) {
  const [copied, setCopied] = useState<"metric" | null>(null);
  const doCopy = (k: "metric", v: string) => {
    navigator.clipboard?.writeText(v);
    setCopied(k);
    setTimeout(() => setCopied(null), 900);
  };
  const statusKind: "ok" | "warn" | "error" | "muted" =
    run.status === "succeeded" ? "ok" :
    run.status === "running" ? "warn" :
    run.status === "failed" ? "error" : "muted";

  return (
    <header
      className="top-header-responsive bg-white border-b border-slate-200 flex items-center gap-5 min-w-0"
      style={{ paddingLeft: 24, paddingRight: 24 }}
    >
      <div className="top-header-brand flex items-center gap-2.5 shrink-0">
        <div className="w-7 h-7 rounded-lg bg-blue-600 flex items-center justify-center text-white">
          <Activity size={16} />
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-[15px] font-semibold text-slate-900">MetricRCA</span>
          <span className="text-[13px] text-slate-500">Investigation Console</span>
        </div>
      </div>

      <div className="top-header-divider h-6 w-px bg-slate-200 shrink-0" />

      <div className="top-header-run-group flex items-center gap-2 min-w-0">
        <span className="text-[12px] text-slate-500 shrink-0">Run</span>
        <select
          aria-label="Run history"
          value={runId}
          disabled={switching || runsIndex.length <= 1}
          title={runsIndex.length <= 1 ? "Run history will appear after multiple diagnoses" : "Load a recent run"}
          onChange={(e) => onRunChange(e.target.value)}
          className="mono text-[13px] bg-white border border-slate-200 text-slate-900 px-2.5 py-1.5 rounded-lg hover:border-slate-300 focus:border-blue-600 focus:outline-none max-w-[190px] min-w-0 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {runsIndex.length === 0 && <option value={runId}>{shortId(runId)} · {run.status}</option>}
          {runsIndex.map((r) => (
            <option key={r.run_id} value={r.run_id}>{shortId(r.run_id)} · {r.status}</option>
          ))}
        </select>
        {switching && <span className="text-[12px] text-blue-600 shrink-0">Loading</span>}
        <CopyableId id={runId} />
        <StatusPill kind={statusKind}>{run.status}</StatusPill>
      </div>

      <div className="top-header-divider h-6 w-px bg-slate-200 shrink-0" />

      <div className="top-header-metric-group flex items-center gap-2 min-w-0">
        <span className="text-[12px] text-slate-500 shrink-0">Metric</span>
        <span className="mono text-[13px] text-slate-900 shrink-0" title={run.metric_id}>{run.metric_id}</span>
        <button
          onClick={() => doCopy("metric", run.metric_id)}
          className="inline-flex items-center justify-center w-8 h-8 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg shrink-0"
          title="Copy metric_id"
        >
          {copied === "metric" ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
        </button>
        <StatusPill kind="ok">OK</StatusPill>
      </div>

      <div className="ml-auto shrink-0">
        <button
          onClick={onRerun}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-blue-600 text-blue-600 bg-white hover:bg-blue-50 rounded-lg text-[13px] font-medium"
        >
          <RefreshCw size={13} /> Re-run
        </button>
      </div>
    </header>
  );
}

function RunMetadataBar({ run }: { run: RunResponse }) {
  return (
    <div
      className="bg-white border-b border-slate-200 flex items-center gap-8 flex-wrap min-w-0"
      style={{ paddingLeft: 24, paddingRight: 24, paddingTop: 18, paddingBottom: 18 }}
    >
      <MetaItem label="Target Date" value={run.target_date} />
      <MetaItem label="Business Today" value={run.business_today} />
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[12px] text-slate-500">{label}</span>
      <span className="mono text-[13px] text-slate-900 truncate" title={value}>{value}</span>
    </div>
  );
}

function SidebarNav({ view, onChange }: { view: ViewKey; onChange: (v: ViewKey) => void }) {
  return (
    <nav className="bg-white border-r border-slate-200 overflow-y-auto flex flex-col" style={{ paddingTop: 20, paddingBottom: 20 }}>
      <div className="px-5 mb-2 text-[11px] font-medium uppercase tracking-wider text-slate-400">Views</div>
      <div className="flex flex-col gap-0.5 px-3">
        {NAV.map((n) => {
          const Icon = n.icon;
          const active = view === n.key;
          return (
            <button
              key={n.key}
              onClick={() => onChange(n.key)}
              className={`relative w-full flex items-center gap-2.5 pl-3 pr-3 py-2 rounded-lg text-[13px] font-medium transition-colors ${
                active
                  ? "bg-blue-50 text-blue-600"
                  : "text-slate-600 hover:text-slate-900 hover:bg-slate-100"
              }`}
            >
              {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full bg-blue-600" />}
              <Icon size={15} className="shrink-0" />
              <span className="truncate">{n.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

function ContextDrawer({
  selection, onClose, onEvidenceClick,
}: {
  selection: GraphSelection; onClose: () => void; onEvidenceClick: (id: string) => void;
}) {
  if (!selection) return null;
  return (
    <aside className="fixed right-0 top-0 bottom-0 z-40 w-[min(560px,calc(100vw-32px))] bg-white border-l border-slate-200 shadow-2xl overflow-y-auto flex flex-col">
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200 shrink-0">
        <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">Trace Detail</div>
        <div className="flex items-center gap-1">
          <button
            onClick={onClose}
            className="inline-flex items-center justify-center w-8 h-8 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="p-5 min-w-0">
        {selection.type === "step"
          ? <StepDrawer step={selection.step} onEvidenceClick={onEvidenceClick} />
          : <LoopDrawer iterations={selection.iterations} />}
      </div>
    </aside>
  );
}

function FailedBanner({ run }: { run: RunResponse }) {
  const err = run.error;
  if (!err) return null;
  return (
    <div className="border border-red-200 bg-red-50 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2 flex-wrap min-w-0">
        <AlertTriangle size={14} className="text-red-600 shrink-0" />
        <span className="mono text-[13px] text-red-700 font-medium">{err.error_code}</span>
        {err.recoverable && <span className="inline-flex items-center px-2 py-0.5 text-[11px] font-medium border rounded-full bg-amber-50 text-amber-700 border-amber-200">recoverable</span>}
        {err.retryable && <span className="inline-flex items-center px-2 py-0.5 text-[11px] font-medium border rounded-full bg-amber-50 text-amber-700 border-amber-200">retryable</span>}
        {err.trace_step_id && <CopyableId id={err.trace_step_id} />}
        {err.retryable && (
          <button className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 border border-blue-600 text-blue-600 bg-white hover:bg-blue-50 rounded-lg text-[12px] font-medium">
            <RotateCcw size={12} /> Retry
          </button>
        )}
      </div>
      <div className="text-[13px] text-slate-900 mb-1">{err.message}</div>
      {err.suggested_next_action && <div className="text-[12px] text-slate-500">→ {err.suggested_next_action}</div>}
    </div>
  );
}

function RerunModal({ onClose, onSubmit }: { onClose: () => void; onSubmit: (payload: RunPayload) => void }) {
  const [q, setQ] = useState(DEFAULT_QUESTION);
  const [targetDate, setTargetDate] = useState("2026-06-05");
  const [businessToday, setBusinessToday] = useState("2026-06-06");
  const [errs, setErrs] = useState<Record<string, string>>({});

  const submit = () => {
    const e: Record<string, string> = {};
    if (!q.trim()) e.question = "Field required (422 detail.loc=[body, question])";
    setErrs(e);
    if (Object.keys(e).length) return;
    onSubmit({
      question: q.trim(),
      target_date: targetDate.trim() || undefined,
      business_today: businessToday.trim() || undefined,
      memory_enabled: false,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-[560px] bg-white border border-slate-200 rounded-2xl shadow-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Search size={15} className="text-blue-600" />
            <span className="text-[14px] font-semibold text-slate-900">New Investigation</span>
            <span className="mono text-[11px] text-slate-400 ml-1">POST /api/rca/runs</span>
          </div>
          <button onClick={onClose} className="inline-flex items-center justify-center w-8 h-8 text-slate-500 hover:text-slate-900 hover:bg-slate-100 rounded-lg">
            <X size={14} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <Field label="Question" required error={errs.question}>
            <textarea
              value={q}
              onChange={(e) => setQ(e.target.value)}
              rows={3}
              placeholder="Why did site-wide GMV drop day-over-day on 2026-06-08?"
              className={`w-full text-[13px] bg-white border ${errs.question ? "border-red-300" : "border-slate-200"} text-slate-900 px-3 py-2 rounded-lg outline-none focus:border-blue-600 placeholder:text-slate-400`}
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Target date">
              <input
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                className="w-full mono text-[13px] bg-white border border-slate-200 text-slate-900 px-3 py-2 rounded-lg outline-none focus:border-blue-600"
              />
            </Field>
            <Field label="Business today">
              <input
                value={businessToday}
                onChange={(e) => setBusinessToday(e.target.value)}
                className="w-full mono text-[13px] bg-white border border-slate-200 text-slate-900 px-3 py-2 rounded-lg outline-none focus:border-blue-600"
              />
            </Field>
          </div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 bg-slate-50 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-3.5 py-2 text-[13px] font-medium text-slate-700 hover:bg-slate-100 rounded-lg">Cancel</button>
          <button onClick={submit} className="inline-flex items-center gap-1.5 px-3.5 py-2 bg-blue-600 text-white hover:bg-blue-700 rounded-lg text-[13px] font-medium">
            <RefreshCw size={13} /> Dispatch run
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, required, error, children }: { label: string; required?: boolean; error?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[12px] text-slate-500 font-medium mb-1.5">
        {label}{required && <span className="text-red-600"> *</span>}
      </div>
      {children}
      {error && <div className="text-[12px] text-red-600 mt-1.5">Warning: {error}</div>}
    </div>
  );
}
