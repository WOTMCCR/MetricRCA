import { useState, ReactNode } from "react";
import { Check, Copy } from "lucide-react";

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`text-[12px] text-slate-500 font-medium ${className}`}>{children}</div>;
}

export function KV({ k, v, mono = false }: { k: string; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <Label>{k}</Label>
      <div className={`min-w-0 truncate ${mono ? "mono text-[13px] text-slate-900" : "text-[13px] text-slate-900"}`}>{v}</div>
    </div>
  );
}

type StatusKind = "ok" | "warn" | "error" | "muted" | "data" | "accent";
const STATUS_CLS: Record<StatusKind, string> = {
  ok: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warn: "bg-amber-50 text-amber-700 border-amber-200",
  error: "bg-red-50 text-red-700 border-red-200",
  muted: "bg-slate-50 text-slate-600 border-slate-200",
  data: "bg-blue-50 text-blue-700 border-blue-200",
  accent: "bg-blue-50 text-blue-700 border-blue-200",
};

export function Badge({ kind = "muted", children, className = "" }: { kind?: StatusKind; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium border rounded-full ${STATUS_CLS[kind]} ${className}`}>
      {children}
    </span>
  );
}

export function StatusPill({ kind = "muted", children }: { kind?: StatusKind; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-[12px] font-medium border rounded-full ${STATUS_CLS[kind]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${kind === "ok" ? "bg-emerald-500" : kind === "warn" ? "bg-amber-500" : kind === "error" ? "bg-red-500" : kind === "accent" || kind === "data" ? "bg-blue-500" : "bg-slate-400"}`} />
      {children}
    </span>
  );
}

export function verdictKind(v?: string): StatusKind {
  if (v === "confirmed") return "ok";
  if (v === "likely") return "warn";
  if (v === "ruled_out") return "muted";
  return "muted";
}

export function CopyButton({ text, label = "" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 900);
      }}
      className="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors px-1.5 py-1 rounded"
      title="Copy"
    >
      {done ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
      {label && <span>{label || (done ? "Copied" : "Copy")}</span>}
    </button>
  );
}

function compactId(id: string) {
  if (id.length <= 16) return id;
  const evidenceSuffix = id.match(/(:E\d+)$/)?.[1];
  if (evidenceSuffix) return `${id.slice(0, 8)}...${evidenceSuffix}`;
  return `${id.slice(0, 8)}...${id.slice(-6)}`;
}

function isLikelyId(value: string) {
  return (
    /^(run|task|step|candidate|evidence|audit|sql|ev|s|t|c|a)[-_/:]/i.test(value) ||
    /^[0-9a-f]{16,}$/i.test(value) ||
    /^[0-9a-f]{8,}:[A-Za-z0-9_-]+$/i.test(value)
  );
}

export function CopyableId({ id, className = "" }: { id: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        navigator.clipboard?.writeText(id);
        setDone(true);
        setTimeout(() => setDone(false), 900);
      }}
      className={`inline-flex max-w-full items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-1.5 py-0.5 align-middle mono text-[11px] font-medium text-blue-700 hover:border-blue-400 hover:bg-blue-100 ${className}`}
      title={`Copy ${id}`}
    >
      <span className="truncate">{done ? "copied" : compactId(id)}</span>
    </button>
  );
}

export function Card({ children, className = "", onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick} className={`bg-white border border-slate-200 rounded-xl ${onClick ? "cursor-pointer hover:border-slate-300 transition-colors" : ""} ${className}`}>
      {children}
    </div>
  );
}

export function SectionHeader({ icon, title, subtitle, right }: { icon?: ReactNode; title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
      <div className="flex items-center gap-2.5 min-w-0">
        {icon && <span className="text-slate-500 shrink-0">{icon}</span>}
        <div className="min-w-0">
          <h3 className="text-[16px] font-semibold text-slate-900 truncate">{title}</h3>
          {subtitle && <div className="text-[13px] text-slate-500 mt-0.5 truncate">{subtitle}</div>}
        </div>
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

export function SegmentedControl<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: Array<{ value: T; label: string }> }) {
  return (
    <div className="inline-flex h-9 p-0.5 bg-slate-100 border border-slate-200 rounded-lg">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            className={`px-3 h-full text-[13px] font-medium rounded-md transition-colors ${active ? "bg-white text-blue-600 shadow-sm" : "text-slate-500 hover:text-slate-900"}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function IconButton({ icon, onClick, title, variant = "ghost" }: { icon: ReactNode; onClick?: () => void; title?: string; variant?: "ghost" | "primary" | "outline" }) {
  const cls = variant === "primary"
    ? "bg-blue-600 text-white hover:bg-blue-700 border-blue-600"
    : variant === "outline"
    ? "bg-white text-blue-600 hover:bg-blue-50 border-blue-600"
    : "text-slate-500 hover:text-slate-900 hover:bg-slate-100 border-transparent";
  return (
    <button onClick={onClick} title={title} className={`inline-flex items-center justify-center w-9 h-9 border rounded-lg transition-colors ${cls}`}>
      {icon}
    </button>
  );
}

export function MetadataChip({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 min-w-0">
      <span className="text-[12px] text-slate-500 shrink-0">{label}</span>
      <span className="text-[13px] text-slate-900 mono truncate min-w-0">{value}</span>
    </div>
  );
}

export function JSONTree({ data, depth = 0 }: { data: any; depth?: number }) {
  if (data === null || data === undefined) return <span className="mono text-[12px] text-slate-400">null</span>;
  if (typeof data === "boolean") return <span className="mono text-[12px] text-amber-700">{String(data)}</span>;
  if (typeof data === "number") return <span className="mono text-[12px] text-blue-700">{data}</span>;
  if (typeof data === "string") {
    if (isLikelyId(data)) return <CopyableId id={data} />;
    return <span className="mono inline-block max-w-full truncate text-[12px] text-slate-900" title={data}>"{data}"</span>;
  }
  if (Array.isArray(data)) {
    if (data.length === 0) return <span className="mono text-[12px] text-slate-400">[]</span>;
    return (
      <div className="space-y-1">
        {data.map((v, i) => (
          <div key={i} className="grid min-w-0 grid-cols-[34px_minmax(0,1fr)] gap-2">
            <span className="mono text-[11px] text-slate-400">[{i}]</span>
            <div className="min-w-0"><JSONTree data={v} depth={depth + 1} /></div>
          </div>
        ))}
      </div>
    );
  }
  const entries = Object.entries(data);
  if (entries.length === 0) return <span className="mono text-[12px] text-slate-400">{"{}"}</span>;
  return (
    <div className="space-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="grid min-w-0 grid-cols-[minmax(72px,0.42fr)_minmax(0,1fr)] gap-2">
          <span className="mono truncate text-[11px] text-slate-500" title={k}>{k}</span>
          <div className="min-w-0"><JSONTree data={v} depth={depth + 1} /></div>
        </div>
      ))}
    </div>
  );
}

export function Bar({ value, max = 100, kind = "ok" }: { value: number; max?: number; kind?: StatusKind }) {
  const pct = Math.max(0, Math.min(100, (Math.abs(value) / max) * 100));
  const fill =
    kind === "ok" ? "bg-emerald-500" :
    kind === "warn" ? "bg-amber-500" :
    kind === "error" ? "bg-red-500" :
    kind === "muted" ? "bg-slate-400" :
    "bg-blue-500";
  return (
    <div className="h-1 bg-slate-200 rounded-full overflow-hidden w-full">
      <div className={`h-full ${fill} rounded-full`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ConfidenceLabel({ value }: { value: number }) {
  const semantic = value >= 0.8 ? "High" : value >= 0.5 ? "Medium" : value >= 0.3 ? "Low" : "Very Low";
  const color = value >= 0.8 ? "text-emerald-600" : value >= 0.5 ? "text-amber-600" : "text-slate-500";
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="mono text-[13px] text-slate-900">{value.toFixed(2)}</span>
      <span className={`text-[12px] font-medium ${color}`}>{semantic}</span>
    </div>
  );
}

export function SQLBlock({ sql }: { sql: string }) {
  const highlight = (line: string) => {
    const tokens = line.split(/(\s+|,|\(|\)|;)/);
    return tokens.map((t, i) => {
      const up = t.toUpperCase().trim();
      if (["SELECT", "FROM", "WHERE", "AND", "OR", "GROUP", "BY", "ORDER", "LIMIT", "AS", "SUM", "AVG", "COUNT", "BETWEEN", "ON", "JOIN", "OVER"].includes(up)) {
        return <span key={i} className="text-purple-700 font-medium">{t}</span>;
      }
      if (/^'.*'$/.test(t)) return <span key={i} className="text-emerald-700">{t}</span>;
      if (/^\d+(\.\d+)?$/.test(t.trim())) return <span key={i} className="text-blue-700">{t}</span>;
      if (/^--/.test(t)) return <span key={i} className="text-slate-400 italic">{t}</span>;
      return <span key={i}>{t}</span>;
    });
  };
  return (
    <pre className="mono text-[12px] bg-slate-50 border border-slate-200 p-3 rounded-lg overflow-x-auto leading-relaxed">
      {sql.split("\n").map((line, i) => (
        <div key={i} className="flex gap-3">
          <span className="text-slate-400 select-none w-6 text-right shrink-0">{i + 1}</span>
          <span className="text-slate-900 whitespace-pre">{highlight(line)}</span>
        </div>
      ))}
    </pre>
  );
}
