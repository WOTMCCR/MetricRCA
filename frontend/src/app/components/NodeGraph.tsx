import {
  Activity,
  AlertTriangle,
  Brain,
  ChartPie,
  Database,
  FileText,
  GitBranch,
  ListTodo,
  MessageCircleQuestion,
  ScanSearch,
  Wrench,
} from "lucide-react";
import type { TraceStep } from "./mockData";

type FlowItem =
  | { kind: "step"; step: TraceStep }
  | { kind: "loop"; iterations: TraceStep[]; firstSeq: number };

export type GraphSelection =
  | { type: "step"; step: TraceStep }
  | { type: "loop"; iterations: TraceStep[] }
  | null;

const ICONS: Record<string, typeof Activity> = {
  parse_question: MessageCircleQuestion,
  read_memory: Brain,
  plan_init: GitBranch,
  react_step: Wrench,
  execute_tool: Activity,
  attribute_rank: ChartPie,
  reflection_verify: ScanSearch,
  generate_report: FileText,
  create_tasks: ListTodo,
  write_memory: Database,
  error_return: AlertTriangle,
};

function aggregateTrace(trace: TraceStep[]): FlowItem[] {
  const out: FlowItem[] = [];
  let loop: TraceStep[] = [];

  const flushLoop = () => {
    if (loop.length > 1) {
      out.push({ kind: "loop", iterations: loop, firstSeq: loop[0].seq });
    } else if (loop.length === 1) {
      out.push({ kind: "step", step: loop[0] });
    }
    loop = [];
  };

  for (const step of trace) {
    if (step.node === "react_step" || step.node === "execute_tool") {
      loop.push(step);
      continue;
    }
    flushLoop();
    out.push({ kind: "step", step });
  }
  flushLoop();

  return out;
}

function stepLabel(step: TraceStep) {
  const labels: Record<string, string> = {
    parse_question: "Parse Question",
    read_memory: "Read Memory",
    plan_init: "Plan",
    attribute_rank: "Rank Cause",
    reflection_verify: "Verify",
    generate_report: "Report",
    create_tasks: "Tasks",
    write_memory: "Write Memory",
    error_return: "Error",
  };
  return labels[step.node] ?? step.node;
}

function stepStatus(step: TraceStep) {
  if (step.error_code) return "error";
  if (step.node === "reflection_verify" && step.output_summary?.repaired) return "warn";
  return "ok";
}

function statusClass(status: "ok" | "warn" | "error") {
  if (status === "error") return "border-red-200 bg-red-50 text-red-700";
  if (status === "warn") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

export function NodeGraph({
  trace,
  selectedStepId,
  onSelect,
}: {
  trace: TraceStep[];
  selectedStepId?: string | null;
  onSelect: (sel: GraphSelection) => void;
}) {
  const items = aggregateTrace(trace);

  if (items.length === 0) {
    return (
      <div className="h-full min-h-[220px] flex items-center justify-center text-[13px] text-slate-500">
        Waiting for trace...
      </div>
    );
  }

  return (
    <div className="p-3">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(138px,1fr))] gap-2">
        {items.map((item) => (
          <div key={item.kind === "loop" ? `loop-${item.firstSeq}` : item.step.step_id} className="min-w-0">
            {item.kind === "loop" ? (
              <LoopBox item={item} selectedStepId={selectedStepId} onSelect={onSelect} />
            ) : (
              <StepBox step={item.step} selected={item.step.step_id === selectedStepId} onSelect={onSelect} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function StepBox({
  step,
  selected,
  onSelect,
}: {
  step: TraceStep;
  selected: boolean;
  onSelect: (sel: GraphSelection) => void;
}) {
  const Icon = ICONS[step.node] ?? Activity;
  const status = stepStatus(step);
  return (
    <button
      type="button"
      onClick={() => onSelect({ type: "step", step })}
      className={`w-full min-h-[78px] rounded-lg border bg-white p-2.5 text-left shadow-sm transition-colors hover:border-blue-300 ${
        selected ? "border-blue-500 ring-2 ring-blue-100" : "border-slate-200"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-blue-50 text-blue-600">
          <Icon size={14} />
        </span>
        <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${statusClass(status)}`}>
          {status}
        </span>
      </div>
      <div className="mt-2 min-w-0">
        <div className="truncate text-[13px] font-semibold text-slate-900">{stepLabel(step)}</div>
        <div className="mt-0.5 mono text-[11px] text-slate-500">#{step.seq} · {step.latency_ms}ms</div>
      </div>
    </button>
  );
}

function LoopBox({
  item,
  selectedStepId,
  onSelect,
}: {
  item: Extract<FlowItem, { kind: "loop" }>;
  selectedStepId?: string | null;
  onSelect: (sel: GraphSelection) => void;
}) {
  const selected = item.iterations.some((step) => step.step_id === selectedStepId);
  const errors = item.iterations.filter((step) => step.error_code).length;
  const tools = item.iterations.filter((step) => step.node === "execute_tool").length;
  return (
    <button
      type="button"
      onClick={() => onSelect({ type: "loop", iterations: item.iterations })}
      className={`w-full min-h-[78px] rounded-lg border border-dashed bg-white p-2.5 text-left shadow-sm transition-colors hover:border-blue-300 ${
        selected ? "border-blue-500 ring-2 ring-blue-100" : "border-slate-300"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
          <Wrench size={14} />
        </span>
        <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${errors ? statusClass("error") : statusClass("ok")}`}>
          {errors ? `${errors} error` : "ok"}
        </span>
      </div>
      <div className="mt-2 min-w-0">
        <div className="truncate text-[13px] font-semibold text-slate-900">ReAct Tool Loop</div>
        <div className="mt-0.5 mono text-[11px] text-slate-500">{tools} tools · {item.iterations.length} steps</div>
      </div>
    </button>
  );
}
