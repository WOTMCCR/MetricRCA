import { useEffect, useState } from "react";
import {
  ChartPie, ScanSearch, Brain, ShieldCheck, ShieldX, FileCheck, FlaskConical,
  ListTodo, AlertTriangle, FileText, Activity, ArrowRight, ExternalLink, Hash, Layers, Loader2, ChevronRight,
} from "lucide-react";
import { Card, Badge, KV, Label, JSONTree, Bar, ConfidenceLabel, CopyableId, SectionHeader, SegmentedControl, SQLBlock, StatusPill, verdictKind } from "./ui-bits";
import type { RunResponse, Evidence, SqlAudit, TraceStep, MemoryRec, TaskRec, EvalSummary, NumericClaim, Candidate } from "./mockData";

/* ============================================================
   VERDICT BAR (Investigation top)
   ============================================================ */

export function VerdictBar({ run, onClaimClick }: { run: RunResponse; onClaimClick: (evidenceId: string) => void }) {
  if (run.status === "no_anomaly") {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-slate-100 flex items-center justify-center text-slate-500">
            <ScanSearch size={18} />
          </div>
          <div className="min-w-0">
            <div className="text-[13px] text-slate-500 mb-0.5">Verdict</div>
            <div className="text-[16px] font-semibold text-slate-900">No significant anomaly detected</div>
            <div className="text-[13px] text-slate-500 mt-1">Metric within 28-day band · no root-cause produced · no operational tasks generated</div>
          </div>
        </div>
      </Card>
    );
  }
  if (run.status === "failed" || !run.report?.top_candidate) return null;

  const tc = run.report.top_candidate;
  return (
    <Card className="p-5">
      <div className="grid grid-cols-12 gap-6 items-start">
        <div className="col-span-12 lg:col-span-6 min-w-0">
          <div className="text-[12px] text-slate-500 mb-1.5">Top candidate · {tc.root_cause_type}</div>
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <span className="text-[20px] font-semibold text-slate-900 mono truncate">{tc.dimension}</span>
            <span className="text-slate-400 mono">=</span>
            <span className="text-[20px] font-semibold text-blue-600 mono truncate">{tc.element}</span>
            <Badge kind={verdictKind(tc.verdict)} className="ml-1">{tc.verdict}</Badge>
          </div>
          {tc.rationale && <div className="text-[13px] text-slate-600 mt-2 leading-relaxed">{tc.rationale}</div>}
        </div>
        <div className="col-span-6 lg:col-span-3 min-w-0">
          <div className="text-[12px] text-slate-500 mb-1.5">Contribution</div>
          <div className="text-[20px] font-semibold text-slate-900 mb-2">{tc.contribution_pct?.toFixed(1) ?? "—"}%</div>
          {tc.contribution_pct != null && <Bar value={tc.contribution_pct} kind="ok" />}
        </div>
        <div className="col-span-6 lg:col-span-3 min-w-0">
          <div className="text-[12px] text-slate-500 mb-1.5">Confidence</div>
          {tc.eng_confidence != null ? <ConfidenceLabel value={tc.eng_confidence} /> : <span className="text-slate-400">—</span>}
        </div>
      </div>
      {run.report.numeric_claims.length > 0 && (
        <div className="border-t border-slate-200 mt-5 pt-4">
          <div className="text-[12px] text-slate-500 mb-2">Numeric claims · click to trace to evidence</div>
          <div className="flex flex-wrap gap-2">
            {run.report.numeric_claims.map((c) => (
              <NumericChip key={c.name} claim={c} onClick={() => onClaimClick(c.evidence_id)} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function NumericChip({ claim, onClick }: { claim: NumericClaim; onClick: () => void }) {
  const isNeg = typeof claim.value === "number" && claim.value < 0;
  return (
    <div
      className="group flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 px-2.5 py-1.5 bg-white border border-slate-200 hover:border-blue-400 hover:bg-blue-50/50 rounded-lg transition-colors text-left"
      title={`Trace to ${claim.evidence_id}`}
    >
      <span className="text-[12px] text-slate-500 shrink-0">{claim.name}</span>
      <span className={`mono text-[13px] font-medium shrink-0 ${isNeg ? "text-red-600" : "text-slate-900"}`}>
        {claim.value}{claim.unit ?? ""}
      </span>
      <CopyableId id={claim.evidence_id} />
      <button type="button" onClick={onClick} className="text-[11px] font-medium text-blue-600 hover:underline">
        Trace
      </button>
    </div>
  );
}

/* ============================================================
   CANDIDATES — strict grid table
   ============================================================ */

const GRID_TEMPLATE =
  "[index] 40px [candidate] minmax(160px,1.2fr) [dim] minmax(220px,1.5fr) [contrib] minmax(160px,1fr) [conf] minmax(120px,0.8fr) [ev] minmax(140px,0.9fr) [act] 32px";

export function CandidatesView({ run, onEvidenceClick }: { run: RunResponse; onEvidenceClick: (id: string) => void }) {
  const cs = run.candidates ?? [];
  const [sortBy, setSortBy] = useState<"contrib" | "conf">("contrib");
  const sorted = [...cs].sort((a, b) =>
    sortBy === "contrib"
      ? (b.contribution_pct ?? 0) - (a.contribution_pct ?? 0)
      : (b.eng_confidence ?? 0) - (a.eng_confidence ?? 0)
  );
  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader
        icon={<ChartPie size={18} />}
        title="Candidates"
        subtitle={`${cs.length} candidate(s) · attribution by shapley_lite`}
        right={
          <div className="flex items-center gap-3">
            <span className="text-[13px] text-slate-500">Sort by</span>
            <SegmentedControl<"contrib" | "conf">
              value={sortBy}
              onChange={setSortBy}
              options={[
                { value: "contrib", label: "Contribution" },
                { value: "conf", label: "Confidence" },
              ]}
            />
          </div>
        }
      />
      {cs.length === 0 ? (
        <Card className="p-8"><EmptyState text="No candidates produced for this run" /></Card>
      ) : (
        <Card className="overflow-hidden">
          <CandidateHeader />
          <div className="divide-y divide-slate-200">
            {sorted.map((c, i) => <CandidateRow key={c.candidate_id} c={c} index={i + 1} onEvidenceClick={onEvidenceClick} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

function CandidateHeader() {
  const cell = "text-[12px] font-medium text-slate-500 min-w-0 truncate";
  return (
    <div className="px-5 py-3 border-b border-slate-200 bg-slate-50/60 grid items-center"
      style={{ gridTemplateColumns: GRID_TEMPLATE, columnGap: 24 }}>
      <div className={cell}>#</div>
      <div className={cell}>Candidate</div>
      <div className={cell}>Dimension / Element</div>
      <div className={cell}>Contribution</div>
      <div className={cell}>Confidence</div>
      <div className={cell}>Evidence</div>
      <div className={cell} />
    </div>
  );
}

function CandidateRow({ c, index, onEvidenceClick }: { c: Candidate; index: number; onEvidenceClick: (id: string) => void }) {
  const ruled = c.verdict === "ruled_out";
  const dimElement = `${c.dimension}=${c.element}`;
  const visible = c.evidence_ids.slice(0, 2);
  const more = c.evidence_ids.length - visible.length;
  return (
    <div className="px-5 py-4 grid items-center hover:bg-slate-50 transition-colors"
      style={{ gridTemplateColumns: GRID_TEMPLATE, columnGap: 24, minHeight: 88 }}>
      {/* index */}
      <div className="min-w-0 text-[13px] text-slate-400 mono">{String(index).padStart(2, "0")}</div>

      {/* candidate */}
      <div className="min-w-0">
        <div className={`text-[14px] font-semibold truncate ${ruled ? "line-through text-slate-400" : "text-slate-900"}`} title={c.root_cause_type}>
          {c.root_cause_type}
        </div>
        <div className="mt-1"><CopyableId id={c.candidate_id} /></div>
      </div>

      {/* dimension / element */}
      <div className="min-w-0">
        <div className="mono text-[14px] text-slate-900 truncate" title={dimElement}>
          <span className={ruled ? "line-through text-slate-400" : ""}>
            <span className="text-slate-500">{c.dimension}</span>
            <span className="text-slate-400">=</span>
            <span>{c.element}</span>
          </span>
        </div>
        <div className="flex items-center gap-1.5 mt-1">
          <Badge kind={verdictKind(c.verdict)}>{c.verdict}</Badge>
          <span className="text-[12px] text-slate-400">({c.dimension})</span>
        </div>
      </div>

      {/* contribution — value + bar in one column */}
      <div className="min-w-0">
        {c.contribution_pct != null ? (
          <>
            <div className="text-[14px] font-semibold text-slate-900">{c.contribution_pct.toFixed(1)}%</div>
            <div className="flex items-center gap-2 mt-1.5">
              <div className="flex-1 min-w-0 max-w-[96px]"><Bar value={c.contribution_pct} kind="ok" /></div>
              <span className="mono text-[12px] text-slate-500 shrink-0">{c.contribution_pct.toFixed(0)}%</span>
            </div>
          </>
        ) : <span className="text-slate-400">—</span>}
      </div>

      {/* confidence */}
      <div className="min-w-0">
        {c.eng_confidence != null ? <ConfidenceLabel value={c.eng_confidence} /> : <span className="text-slate-400">—</span>}
      </div>

      {/* evidence */}
      <div className="min-w-0 flex flex-col gap-1">
        {c.evidence_ids.length === 0 ? (
          <span className="text-slate-400 text-[13px]">—</span>
        ) : (
          <>
            {visible.map((id) => (
              <div key={id} className="flex min-w-0 items-center gap-1.5">
                <CopyableId id={id} />
                <button type="button" onClick={() => onEvidenceClick(id)} className="text-[11px] text-blue-600 hover:underline">
                  Trace
                </button>
              </div>
            ))}
            {more > 0 && <span className="text-[12px] text-slate-500">+{more} more</span>}
          </>
        )}
      </div>

      {/* action */}
      <div className="min-w-0 flex justify-end">
        <ChevronRight size={16} className="text-slate-400" />
      </div>
    </div>
  );
}

/* ============================================================
   EVIDENCE
   ============================================================ */

export function EvidenceView({ items, highlightId, onLocateStep }: { items: Evidence[]; highlightId?: string | null; onLocateStep: (stepId: string) => void }) {
  const [selectedId, setSelectedId] = useState(highlightId ?? items[0]?.evidence_id ?? null);
  const selected = items.find((item) => item.evidence_id === selectedId) ?? items[0];

  useEffect(() => {
    if (highlightId) setSelectedId(highlightId);
  }, [highlightId]);

  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader
        icon={<FileCheck size={18} />}
        title="Evidence"
        subtitle={`${items.length} query-backed record(s) · each numeric claim must point here`}
      />
      <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-4 items-start">
        <Card className="overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-200 bg-slate-50/70">
            <div className="text-[13px] font-semibold text-slate-900">Evidence index</div>
            <div className="text-[12px] text-slate-500 mt-0.5">Select a record to inspect SQL, QuerySpec, and result summary.</div>
          </div>
          <div className="divide-y divide-slate-200">
            {items.map((e, index) => (
              <EvidenceIndexRow
                key={e.evidence_id}
                evidence={e}
                index={index}
                active={e.evidence_id === selected?.evidence_id}
                highlighted={e.evidence_id === highlightId}
                onSelect={() => setSelectedId(e.evidence_id)}
              />
            ))}
          </div>
        </Card>
        {selected ? (
          <EvidenceDetail evidence={selected} onLocateStep={onLocateStep} />
        ) : (
          <Card className="p-8"><EmptyState text="No evidence records" /></Card>
        )}
      </div>
    </div>
  );
}

function EvidenceIndexRow({
  evidence,
  index,
  active,
  highlighted,
  onSelect,
}: {
  evidence: Evidence;
  index: number;
  active: boolean;
  highlighted: boolean;
  onSelect: () => void;
}) {
  const purpose = String(evidence.query_spec?.purpose ?? "query");
  const metricId = String(evidence.query_spec?.metric_id ?? "metric");
  const summary = evidenceSummaryItems(evidence.result_summary).slice(0, 2);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={`w-full cursor-pointer p-3 text-left transition-colors hover:bg-blue-50/40 ${
        active ? "bg-blue-50/70" : highlighted ? "bg-amber-50/50" : "bg-white"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="mono text-[11px] text-slate-400">#{index + 1}</span>
          <span id={`ev-${evidence.evidence_id}`}><CopyableId id={evidence.evidence_id} /></span>
        </div>
        {evidence.guard_status === "passed" ? (
          <Badge kind="ok"><ShieldCheck size={11} /> passed</Badge>
        ) : (
          <Badge kind="error"><ShieldX size={11} /> {evidence.guard_status}</Badge>
        )}
      </div>
      <div className="mt-2 flex items-center gap-1.5 flex-wrap">
        <Badge kind="accent">{purpose}</Badge>
        <Badge kind="muted">{metricId}</Badge>
        <Badge kind="muted">{evidence.data_source}</Badge>
      </div>
      {summary.length > 0 && (
        <div className="mt-2 grid grid-cols-1 gap-1.5">
          {summary.map(([key, value]) => (
            <div key={key} className="min-w-0 rounded-md bg-white/70 border border-slate-200 px-2 py-1">
              <div className="truncate text-[11px] text-slate-500">{key}</div>
              <div className="mono truncate text-[12px] text-slate-900">{formatEvidenceValue(value)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EvidenceDetail({ evidence, onLocateStep }: { evidence: Evidence; onLocateStep: (stepId: string) => void }) {
  const purpose = String(evidence.query_spec?.purpose ?? "query");
  const metricId = String(evidence.query_spec?.metric_id ?? "metric");
  const summary = evidenceSummaryItems(evidence.result_summary).slice(0, 8);
  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-200 bg-white">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <FileCheck size={16} className="text-slate-500 shrink-0" />
              <CopyableId id={evidence.evidence_id} className="text-[12px]" />
              {evidence.guard_status === "passed" ? (
                <Badge kind="ok"><ShieldCheck size={11} /> guard passed</Badge>
              ) : (
                <Badge kind="error"><ShieldX size={11} /> guard {evidence.guard_status}</Badge>
              )}
            </div>
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <Badge kind="accent">{purpose}</Badge>
              <Badge kind="muted">{metricId}</Badge>
              <Badge kind="muted">{evidence.data_source}</Badge>
              <span className="mono text-[11px] text-slate-500">{evidence.created_at}</span>
            </div>
          </div>
          {evidence.produced_by_step && (
            <button
              type="button"
              onClick={() => onLocateStep(evidence.produced_by_step!)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-[13px] font-medium text-blue-700 hover:border-blue-400"
            >
              <ExternalLink size={12} /> Locate trace step
            </button>
          )}
        </div>
      </div>
      <div className="p-5 space-y-5">
        <div className="flex items-center gap-2 text-[12px]">
          <Hash size={12} className="text-slate-400" />
          <span className="text-slate-500">SQL hash</span>
          <CopyableId id={evidence.sql_hash} />
        </div>
        {summary.length > 0 && (
          <div>
            <Label className="mb-2">Result summary</Label>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {summary.map(([key, value]) => (
                <div key={key} className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 min-w-0">
                  <div className="truncate text-[12px] text-slate-500" title={key}>{key}</div>
                  <div className="mt-1 mono truncate text-[14px] font-semibold text-slate-900" title={String(value)}>
                    {formatEvidenceValue(value)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)] gap-4">
          <div className="min-w-0">
            <Label className="mb-1.5">SQL</Label>
            <SQLBlock sql={evidence.sql_text} />
          </div>
          <div className="min-w-0 space-y-4">
            <div>
              <Label className="mb-1.5">QuerySpec</Label>
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg overflow-x-auto"><JSONTree data={evidence.query_spec} /></div>
            </div>
            <div>
              <Label className="mb-1.5">Raw result</Label>
              <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg overflow-x-auto"><JSONTree data={evidence.result_summary} /></div>
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

function evidenceSummaryItems(summary: Record<string, any>) {
  return Object.entries(summary ?? {}).filter(([, value]) =>
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  );
}

function formatEvidenceValue(value: unknown) {
  if (typeof value === "number") {
    if (Math.abs(value) > 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/* ============================================================
   SQL AUDIT
   ============================================================ */

export function SQLAuditView({ items }: { items: SqlAudit[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const rejected = items.filter((i) => i.guard_status === "rejected").length;
  const AUDIT_GRID = "120px 80px minmax(160px,1.2fr) 80px 100px minmax(180px,1.4fr) 60px";
  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader
        icon={<ShieldCheck size={18} />}
        title="SQL Audit"
        subtitle={`${items.length} queries · ${rejected} rejected by guard`}
        right={rejected > 0 ? <Badge kind="error">{rejected} REJECTED</Badge> : <Badge kind="ok">All passed</Badge>}
      />
      <Card className="overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-200 bg-slate-50/60 grid items-center text-[12px] font-medium text-slate-500"
          style={{ gridTemplateColumns: AUDIT_GRID, columnGap: 16 }}>
          <div>audit_id</div>
          <div>guard</div>
          <div>sql_hash</div>
          <div>rows</div>
          <div>latency</div>
          <div>created_at</div>
          <div />
        </div>
        {items.map((a) => (
          <div key={a.audit_id} className={`border-b border-slate-200 last:border-b-0 ${a.guard_status === "rejected" ? "bg-red-50/40" : ""}`}>
            <div className="px-5 py-3 grid items-center hover:bg-slate-50 transition-colors"
              style={{ gridTemplateColumns: AUDIT_GRID, columnGap: 16 }}>
              <div className="mono text-[13px] text-slate-900 truncate">{a.audit_id}</div>
              <div>{a.guard_status === "passed" ? <Badge kind="ok"><ShieldCheck size={11} /></Badge> : <Badge kind="error"><ShieldX size={11} /></Badge>}</div>
              <div className="min-w-0"><CopyableId id={a.sql_hash} /></div>
              <div className="mono text-[13px] text-slate-900">{a.row_count}</div>
              <div className="mono text-[13px] text-slate-500">{a.latency_ms}ms</div>
              <div className="mono text-[12px] text-slate-500 truncate">{a.created_at}</div>
              <div className="text-right">
                <button onClick={() => setExpanded(expanded === a.audit_id ? null : a.audit_id)} className="text-[12px] text-blue-600 hover:underline">{expanded === a.audit_id ? "Hide" : "View"}</button>
              </div>
            </div>
            {expanded === a.audit_id && (
              <div className="p-5 bg-slate-50/60 grid grid-cols-1 lg:grid-cols-12 gap-4">
                <div className="lg:col-span-8 min-w-0"><Label className="mb-1.5">sql_text</Label><SQLBlock sql={a.sql_text} /></div>
                <div className="lg:col-span-4 min-w-0">
                  <Label className="mb-1.5">guard_errors</Label>
                  {a.guard_errors.length === 0 ? <div className="text-[13px] text-emerald-600">No violations</div> : (
                    <ul className="space-y-1.5">
                      {a.guard_errors.map((er) => (
                        <li key={er} className="text-[12px] text-red-700 border border-red-200 bg-red-50 px-2.5 py-1.5 rounded-md flex items-center gap-1.5">
                          <AlertTriangle size={12} /> <span className="mono">{er}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}

/* ============================================================
   REFLECTION
   ============================================================ */

export function ReflectionView({ trace }: { trace: TraceStep[] }) {
  const r = trace.find((s) => s.node === "reflection_verify");
  if (!r) return <Card className="p-8"><EmptyState text="reflection_verify not executed for this run" /></Card>;
  const out = r.output_summary as any;
  const issues: any[] = out?.issues ?? [];
  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader icon={<ScanSearch size={18} />} title="Reflection" subtitle="Deterministic rules + LLM joint verification" />
      <Card className="p-5">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
          <KV k="Passed" v={<Badge kind={out?.passed ? "ok" : "error"}>{String(out?.passed)}</Badge>} />
          <KV k="Repaired" v={<Badge kind={out?.repaired ? "warn" : "muted"}>{String(out?.repaired ?? false)}</Badge>} />
          <KV k="Repair count" v={<span className="mono text-slate-900">{out?.repair_count ?? 0}</span>} />
          <KV k="Latency" v={<span className="mono text-slate-900">{r.latency_ms}ms</span>} />
        </div>
        <Label className="mb-2">Issues ({issues.length})</Label>
        {issues.length === 0 ? <EmptyState text="No verification issues" /> : (
          <div className="space-y-2">
            {issues.map((it, i) => (
              <div key={i} className="border border-slate-200 rounded-lg p-3 bg-slate-50/60">
                <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                  <Badge kind={it.severity === "error" ? "error" : "warn"}>{it.severity}</Badge>
                  <Badge kind={it.by === "rule" ? "accent" : "muted"}>{it.by === "rule" ? "rule (deterministic)" : "llm"}</Badge>
                  <span className="mono text-[13px] text-slate-900">{it.check}</span>
                </div>
                <div className="text-[13px] text-slate-700">{it.message}</div>
                {it.suggested_action && <div className="text-[12px] text-slate-500 mt-1">→ suggested: {it.suggested_action}</div>}
              </div>
            ))}
          </div>
        )}
        {out?.repair_count > 0 && (
          <div className="mt-5 border-t border-slate-200 pt-4">
            <Label className="mb-2">Repair timeline</Label>
            <div className="flex items-center gap-2 text-[13px] flex-wrap">
              <span className="px-2 py-1 bg-red-50 text-red-700 border border-red-200 rounded">missing evidence_id</span>
              <ArrowRight size={14} className="text-slate-400" />
              <span className="px-2 py-1 bg-amber-50 text-amber-700 border border-amber-200 rounded">re-bind ev_005</span>
              <ArrowRight size={14} className="text-slate-400" />
              <span className="px-2 py-1 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded">verified</span>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ============================================================
   MEMORY
   ============================================================ */

export function MemoryView({ items }: { items: MemoryRec[] }) {
  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader icon={<Brain size={18} />} title="Memory" subtitle="Read/write traces · hints only influence plan priority, not conclusions" />
      <Card className="p-5">
        <div className="border-l-2 border-slate-200 pl-5 space-y-4">
          {items.map((m) => (
            <div key={m.step_id} className="relative">
              <div className="absolute -left-[26px] top-1.5 w-2.5 h-2.5 rounded-full bg-blue-500 border-2 border-white" />
              <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                <Badge kind={m.node === "read_memory" ? "accent" : "muted"}>{m.node}</Badge>
                <CopyableId id={m.step_id} />
                <span className="mono text-[12px] text-slate-400">{m.created_at}</span>
                {m.error_code && <Badge kind="error">{m.error_code}</Badge>}
              </div>
              <div className="p-3 bg-slate-50 border border-dashed border-slate-300 rounded-lg">
                <Label className="mb-1">hint (priority bias, not fact)</Label>
                <JSONTree data={m.output_summary} />
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/* ============================================================
   TASKS
   ============================================================ */

export function TasksView({ items, run }: { items: TaskRec[]; run: RunResponse }) {
  if (run.status === "no_anomaly") {
    return (
      <div className="space-y-4">
        <SectionHeader icon={<ListTodo size={18} />} title="Tasks" />
        <Card className="p-8"><EmptyState text="No anomaly · no operational tasks generated" /></Card>
      </div>
    );
  }
  return (
    <div className="space-y-4 min-w-0">
      <SectionHeader icon={<ListTodo size={18} />} title="Tasks" subtitle={`${items.length} actionable item(s) emitted`} />
      {items.length === 0 ? <Card className="p-8"><EmptyState text="No tasks for this run" /></Card> : (
        <div className="grid grid-cols-1 gap-4">
          {items.map((t) => (
            <Card key={t.task_id} className="p-5">
              <div className="flex items-center justify-between mb-3">
                <Badge kind="accent">{t.root_cause_type}</Badge>
                <CopyableId id={t.task_id} />
              </div>
              <div className="text-[15px] font-semibold text-slate-900 mb-3">{t.title}</div>
              <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_220px] gap-4 items-start">
                <div className="min-w-0">
                  <Label className="mb-1.5">Payload</Label>
                  <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg overflow-x-auto"><JSONTree data={t.payload} /></div>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 min-w-0">
                  <KV k="Created" v={t.created_at} mono />
                  <div className="mt-3">
                    <Label className="mb-1">Task id</Label>
                    <CopyableId id={t.task_id} />
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

/* ============================================================
   EVAL BOARD
   ============================================================ */

export function EvalBoard({ data }: { data: EvalSummary }) {
  const rates = [
    { k: "top1_rate", v: data.top1_rate },
    { k: "top3_rate", v: data.top3_rate },
    { k: "anomaly_accuracy", v: data.anomaly_accuracy },
    { k: "evidence_coverage_avg", v: data.evidence_coverage_avg },
    { k: "sql_safe_rate", v: data.sql_safe_rate },
    { k: "report_traceable_rate", v: data.report_traceable_rate },
  ];
  const bools = [
    { k: "reflection_repair_ok", v: data.reflection_repair_ok },
    { k: "memory_pollution_ok", v: data.memory_pollution_ok },
    { k: "dangerous_sql_blocked", v: data.dangerous_sql_blocked },
    { k: "no_anomaly_correct", v: data.no_anomaly_correct },
    { k: "thresholds_met", v: data.thresholds_met },
  ];
  return (
    <div className="space-y-4 min-w-0">
      {!data.thresholds_met && (
        <div className="border border-red-200 bg-red-50 rounded-lg p-3 text-[13px] text-red-700 flex items-center gap-2">
          <AlertTriangle size={14} /> Thresholds not met — eval gate failed
        </div>
      )}
      <SectionHeader
        icon={<FlaskConical size={18} />}
        title="Quality Eval"
        subtitle={`${data.eval_id} · ${data.case_total} regression cases · not part of the user RCA flow`}
      />
      <Card className="p-5">
        <Label className="mb-3">Ratios</Label>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6">
          {rates.map((r) => <RatioCell key={r.k} k={r.k} v={r.v} />)}
        </div>
        <Label className="mb-3">Boolean gates</Label>
        <div className="flex flex-wrap gap-2 mb-6">
          {bools.map((b) => <Badge key={b.k} kind={b.v ? "ok" : "error"}>{b.k} · {b.v ? "PASS" : "FAIL"}</Badge>)}
        </div>
        <Label className="mb-3">Cases</Label>
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          <div className="grid grid-cols-12 gap-3 px-4 py-2.5 bg-slate-50 border-b border-slate-200 text-[12px] font-medium text-slate-500">
            <div className="col-span-2">case_id</div>
            <div className="col-span-2">metric_id</div>
            <div className="col-span-3">expected</div>
            <div className="col-span-3">actual</div>
            <div className="col-span-2">passed</div>
          </div>
          {data.cases.map((c) => (
            <div key={c.case_id} className="grid grid-cols-12 gap-3 px-4 py-2.5 border-b border-slate-200 last:border-b-0 text-[13px]">
              <div className="col-span-2 mono text-slate-900 truncate">{c.case_id}</div>
              <div className="col-span-2 mono text-blue-600 truncate">{c.metric_id}</div>
              <div className="col-span-3 text-slate-500 truncate">{c.expected}</div>
              <div className="col-span-3 text-slate-900 truncate">{c.actual}</div>
              <div className="col-span-2">{c.passed ? <Badge kind="ok">PASS</Badge> : <Badge kind="error">FAIL</Badge>}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function RatioCell({ k, v }: { k: string; v: number }) {
  const pct = v * 100;
  const kind = v >= 0.9 ? "ok" : v >= 0.7 ? "warn" : "error";
  const ringColor = kind === "ok" ? "#059669" : kind === "warn" ? "#D97706" : "#DC2626";
  const r = 22;
  const c = 2 * Math.PI * r;
  return (
    <div className="border border-slate-200 rounded-lg p-4 bg-white flex items-center gap-3 min-w-0">
      <svg width="56" height="56" viewBox="0 0 56 56" className="shrink-0">
        <circle cx="28" cy="28" r={r} stroke="#E5E7EB" strokeWidth="4" fill="none" />
        <circle cx="28" cy="28" r={r} stroke={ringColor} strokeWidth="4" fill="none"
          strokeDasharray={`${(pct / 100) * c} ${c}`} strokeLinecap="round" transform="rotate(-90 28 28)" />
        <text x="28" y="32" textAnchor="middle" fontSize="11" fill="#0F172A" fontWeight="600">{pct.toFixed(0)}%</text>
      </svg>
      <div className="min-w-0">
        <div className="text-[12px] text-slate-500 truncate">{k}</div>
        <div className="mono text-[15px] font-semibold text-slate-900">{v.toFixed(3)}</div>
      </div>
    </div>
  );
}

/* ============================================================
   STEP / LOOP DRAWER
   ============================================================ */

export function StepDrawer({ step, onEvidenceClick }: { step: TraceStep; onEvidenceClick: (id: string) => void }) {
  const isError = !!step.error_code;
  const evidenceId =
    (step.output_summary as any)?.evidence_id ||
    ((step.output_summary as any)?.evidence_ids?.[0]);
  return (
    <div className="space-y-4 min-w-0">
      <div className="flex items-center gap-2 flex-wrap min-w-0">
        <Activity size={16} className="text-slate-500 shrink-0" />
        <span className="text-[16px] font-semibold text-slate-900 truncate">{step.node}</span>
        {isError ? <Badge kind="error">{step.error_code}</Badge> : <Badge kind="ok">ok</Badge>}
        <Badge kind="muted">{step.latency_ms}ms</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <KV k="step_id" v={<CopyableId id={step.step_id} />} />
        <KV k="seq" v={`#${step.seq}`} mono />
        <KV k="action" v={step.action} mono />
        <KV k="created_at" v={step.created_at} mono />
      </div>
      <div>
        <Label className="mb-1.5">input_summary</Label>
        <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg overflow-x-auto"><JSONTree data={step.input_summary} /></div>
      </div>
      <div>
        <Label className="mb-1.5">output_summary</Label>
        <div className="p-3 bg-slate-50 border border-slate-200 rounded-lg overflow-x-auto"><JSONTree data={step.output_summary} /></div>
      </div>
      {isError && (
        <div className="border border-red-200 bg-red-50 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle size={14} className="text-red-600" />
            <span className="text-[13px] font-semibold text-red-700">{step.error_code}</span>
          </div>
          <div className="text-[13px] text-slate-700">Step failed. See error_return / final report.</div>
        </div>
      )}
      {evidenceId && (
        <button onClick={() => onEvidenceClick(evidenceId)} className="w-full px-3 py-2.5 border border-blue-200 hover:border-blue-400 bg-blue-50/50 rounded-lg text-left transition-colors">
          <div className="text-[12px] text-slate-500 mb-0.5">Related evidence</div>
          <div className="flex items-center gap-2">
            <FileCheck size={14} className="text-blue-600 shrink-0" />
            <CopyableId id={evidenceId} className="flex-1" />
            <ArrowRight size={14} className="text-blue-400 shrink-0" />
          </div>
        </button>
      )}
    </div>
  );
}

export function LoopDrawer({ iterations }: { iterations: TraceStep[] }) {
  return (
    <div className="space-y-4 min-w-0">
      <div className="flex items-center gap-2 flex-wrap">
        <Layers size={16} className="text-slate-500" />
        <span className="text-[16px] font-semibold text-slate-900">ReAct Loop</span>
        <Badge kind="muted">{iterations.length} iters</Badge>
        <Badge kind="muted">{iterations.reduce((s, it) => s + it.latency_ms, 0)}ms</Badge>
      </div>
      <div className="space-y-2">
        {iterations.map((it) => (
          <div key={it.step_id} className="border border-slate-200 rounded-lg p-3 bg-slate-50/40 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="mono text-[12px] text-slate-500">#{it.seq}</span>
              <Badge kind={it.node === "react_step" ? "accent" : "muted"}>{it.node}</Badge>
              <span className="mono text-[13px] text-slate-900 truncate flex-1 min-w-0">{it.action}</span>
              <span className="mono text-[12px] text-slate-500 shrink-0">{it.latency_ms}ms</span>
            </div>
            <div className="p-2 bg-white border border-slate-200 rounded overflow-x-auto"><JSONTree data={it.output_summary} /></div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function EmptyState({ text }: { text: string }) {
  return (
    <div className="text-center py-6 text-[13px] text-slate-500 flex items-center justify-center gap-2">
      <Loader2 size={14} className="opacity-40" /> {text}
    </div>
  );
}

export function ConclusionCard({
  run,
  evidence,
  onEvidenceClick,
}: {
  run: RunResponse;
  evidence: Evidence[];
  onEvidenceClick: (id: string) => void;
}) {
  if (!run.report?.narrative && !run.report?.top_candidate) return null;
  const candidate = run.report?.top_candidate;
  const isGenerated = run.status !== "running" && (Boolean(candidate) || run.status === "no_anomaly");
  const contribution = candidate?.contribution_pct;
  const evidenceIds = candidate?.evidence_ids ?? run.report?.numeric_claims.map((claim) => claim.evidence_id) ?? [];
  const uniqueEvidenceIds = Array.from(new Set(evidenceIds.filter(Boolean)));
  const evidenceById = new Map(evidence.map((item) => [item.evidence_id, item]));
  return (
    <Card className="overflow-hidden border-blue-200">
      <div className="border-b border-blue-100 bg-blue-50/60 px-5 py-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
              <FileText size={17} />
            </div>
            <div className="min-w-0">
              <div className="text-[16px] font-semibold text-slate-900">
                {isGenerated ? "Generated RCA Report" : "Investigation Status"}
              </div>
              <div className="text-[12px] text-slate-600 mt-0.5">
                {isGenerated ? "Evidence-backed conclusion for this run" : "The RCA report will appear here after the run completes"}
              </div>
            </div>
          </div>
          <Badge kind={isGenerated ? "ok" : "warn"}>{isGenerated ? "report generated" : run.status}</Badge>
        </div>
      </div>
      <div className="p-5">
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_280px] gap-5 items-start">
          {candidate ? (
            <div className="min-w-0 space-y-4">
              <div>
                <div className="text-[12px] font-medium uppercase tracking-wide text-slate-500 mb-2">Conclusion</div>
                <div className="text-[22px] font-semibold leading-tight text-slate-950">
                  <span className="mono">{candidate.dimension}</span>
                  <span className="mx-2 text-slate-400">=</span>
                  <span className="mono text-blue-600">{candidate.element}</span>
                </div>
                <div className="mt-2 flex items-center gap-2 flex-wrap">
                  <Badge kind={verdictKind(candidate.verdict)}>{candidate.verdict}</Badge>
                  <Badge kind="accent">{candidate.root_cause_type}</Badge>
                </div>
              </div>
              <div className="text-[14px] leading-relaxed text-slate-700">
                The RCA run identified <span className="font-semibold text-slate-900">{candidate.dimension}={candidate.element}</span> as the strongest confirmed driver of the metric movement.
                {contribution != null && (
                  <> Its attributed contribution is <span className="font-semibold text-slate-900">{contribution.toFixed(1)}%</span>. </>
                )}
                This report is generated from persisted, guard-passed evidence and can be audited from the Evidence and SQL Audit views.
              </div>
            </div>
          ) : (
            <div className="text-[14px] text-slate-700 leading-relaxed">{run.report.narrative}</div>
          )}
          {candidate && (
            <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 min-w-0">
              <div className="grid grid-cols-2 gap-4">
                <KV k="Contribution" v={contribution != null ? `${contribution.toFixed(1)}%` : "—"} />
                <KV k="Confidence" v={candidate.eng_confidence != null ? <ConfidenceLabel value={candidate.eng_confidence} /> : "—"} />
              </div>
              <div className="mt-4 border-t border-slate-200 pt-3">
                <Label className="mb-1">Recommended next step</Label>
                <div className="text-[13px] leading-relaxed text-slate-700">
                  Review the bound evidence, then assign the generated task to investigate the affected {candidate.dimension} segment.
                </div>
              </div>
            </div>
          )}
        </div>
        {candidate && uniqueEvidenceIds.length > 0 && (
          <div className="mt-5 border-t border-slate-200 pt-4">
            <div className="mb-3 flex items-end justify-between gap-3 flex-wrap">
              <div>
                <Label>Bound evidence</Label>
                <div className="mt-0.5 text-[12px] text-slate-500">Guard-passed evidence bound to the confirmed candidate.</div>
              </div>
              <button
                type="button"
                onClick={() => onEvidenceClick(uniqueEvidenceIds[0])}
                className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 text-[12px] font-medium text-blue-700 hover:border-blue-400"
              >
                <ExternalLink size={12} /> Open evidence view
              </button>
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
              {uniqueEvidenceIds.slice(0, 4).map((id) => (
                <ReportEvidenceSummary
                  key={id}
                  evidenceId={id}
                  evidence={evidenceById.get(id)}
                  onOpen={() => onEvidenceClick(id)}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function ReportEvidenceSummary({
  evidenceId,
  evidence,
  onOpen,
}: {
  evidenceId: string;
  evidence?: Evidence;
  onOpen: () => void;
}) {
  const alias = evidenceId.split(":").pop() ?? evidenceId;
  const querySpec = evidence?.query_spec ?? {};
  const purpose = String(querySpec.purpose ?? "evidence");
  const metricId = String(querySpec.metric_id ?? "metric");
  const summaryItems = evidence ? evidenceSummaryItems(evidence.result_summary).slice(0, 4) : [];
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 min-w-0">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Badge kind="accent">{alias}</Badge>
          <CopyableId id={evidenceId} />
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          {evidence ? (
            evidence.guard_status === "passed"
              ? <Badge kind="ok"><ShieldCheck size={11} /> guard passed</Badge>
              : <Badge kind="error"><ShieldX size={11} /> guard {evidence.guard_status}</Badge>
          ) : (
            <Badge kind="warn">not loaded</Badge>
          )}
          <button
            type="button"
            onClick={onOpen}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-blue-600 hover:bg-blue-50"
          >
            <ExternalLink size={11} /> Open
          </button>
        </div>
      </div>
      {evidence ? (
        <>
          <div className="mt-2 flex items-center gap-1.5 overflow-hidden">
            <Badge kind="muted" className="shrink-0">{purpose}</Badge>
            <Badge kind="muted" className="shrink-0">{metricId}</Badge>
            <Badge kind="muted" className="shrink-0">{evidence.data_source}</Badge>
            {evidence.sql_hash && <CopyableId id={evidence.sql_hash} className="min-w-0" />}
          </div>
          {summaryItems.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {summaryItems.map(([key, value]) => (
                <div key={key} className="inline-flex max-w-full items-baseline gap-1.5 rounded-md border border-slate-200 bg-slate-50/70 px-2 py-1">
                  <span className="truncate text-[11px] text-slate-500" title={key}>{key}</span>
                  <span className="mono max-w-[150px] truncate text-[12px] font-medium text-slate-900" title={String(value)}>
                    {formatEvidenceValue(value)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="mt-2 text-[12px] text-slate-500">This evidence id is bound to the report, but its persisted detail is not loaded in the current UI bundle.</div>
      )}
    </div>
  );
}
