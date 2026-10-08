"use client";

import { Bot, ChevronRight, Cpu, Database, Hand, Search, Shield, Workflow } from "lucide-react";
import { useMemo, useState } from "react";
import type { Span } from "@/lib/types";
import { cn, fmtMs } from "@/lib/utils";

const KIND: Record<string, { icon: typeof Bot; tone: string; label: string }> = {
  node: { icon: Workflow, tone: "bg-navy dark:bg-stone", label: "Agent" },
  llm: { icon: Bot, tone: "bg-accent", label: "LLM" },
  tool: { icon: Database, tone: "bg-stone-deep", label: "Tool" },
  guard: { icon: Shield, tone: "bg-positive", label: "Guard" },
  retrieval: { icon: Search, tone: "bg-[#4b4f8f]", label: "Retrieval" },
  human: { icon: Hand, tone: "bg-warn", label: "Human" },
  sandbox: { icon: Cpu, tone: "bg-[#2f6f8f]", label: "Sandbox" },
};

/** Waterfall of trace.v1 spans: nesting, timing bars, model/tokens, and the redacted I/O on demand. */
export function TraceTimeline({ spans, compact = false }: { spans: Span[]; compact?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  const { rows, t0, total } = useMemo(() => {
    const starts = spans.map((s) => new Date(s.started_at).getTime());
    const t0 = Math.min(...starts);
    const end = Math.max(...spans.map((s, i) => starts[i] + s.duration_ms));
    const depth = new Map<string, number>();
    const rows = spans.map((s, i) => {
      const d = s.parent_id ? (depth.get(s.parent_id) ?? 0) + 1 : 0;
      depth.set(s.span_id, d);
      return { s, d, start: starts[i] };
    });
    return { rows, t0, total: Math.max(1, end - t0) };
  }, [spans]);
  if (!spans.length) return <p className="text-sm text-muted">No spans recorded.</p>;
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-3">
        {Object.entries(KIND).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1.5 text-[0.7rem] text-muted">
            <span className={cn("size-2 rounded-full", v.tone)} /> {v.label}
          </span>
        ))}
      </div>
      <ol className="divide-y divide-line rounded-xl border border-line">
        {rows.map(({ s, d, start }) => {
          const k = KIND[s.kind] ?? KIND.node;
          const Icon = k.icon;
          const left = ((start - t0) / total) * 100;
          const width = Math.max(0.6, (s.duration_ms / total) * 100);
          const isOpen = open === s.span_id;
          return (
            <li key={s.span_id}>
              <button type="button" onClick={() => setOpen(isOpen ? null : s.span_id)} aria-expanded={isOpen} className="grid w-full grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] items-center gap-3 px-3 py-2 text-left text-xs hover:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_5rem]">
                <span className="flex min-w-0 items-center gap-2" style={{ paddingLeft: Math.min(d, 6) * 14 }}>
                  <ChevronRight className={cn("size-3 shrink-0 text-muted transition-transform", isOpen && "rotate-90")} />
                  <span className={cn("grid size-5 shrink-0 place-items-center rounded-full text-white", k.tone)}>
                    <Icon className="size-3" />
                  </span>
                  <span className="truncate font-semibold">{s.name}</span>
                  {s.status !== "ok" && <span className={cn("rounded-full px-1.5 font-mono text-[0.58rem] uppercase", s.status === "blocked" ? "bg-warn-soft text-warn" : "bg-accent-soft text-accent-ink")}>{s.status}</span>}
                  {!compact && s.model && <span className="hidden truncate font-mono text-[0.62rem] text-muted md:inline">{s.model}</span>}
                </span>
                <span className="relative h-2 rounded-full bg-surface-sunk">
                  <span className={cn("absolute top-0 h-2 rounded-full", k.tone)} style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
                <span className="hidden text-right font-mono tabular-nums text-muted sm:block">{fmtMs(s.duration_ms)}</span>
              </button>
              {isOpen && (
                <div className="grid gap-3 bg-surface-2/60 px-4 py-3 text-xs sm:grid-cols-2">
                  <dl className="space-y-1">
                    <Row k="kind" v={s.kind} />
                    {s.provider && <Row k="provider" v={`${s.provider} · ${s.model}`} />}
                    {(s.tokens_in || s.tokens_out) > 0 && <Row k="tokens" v={`${s.tokens_in} in · ${s.tokens_out} out`} />}
                    <Row k="duration" v={fmtMs(s.duration_ms)} />
                    {s.error && <Row k="error" v={s.error} />}
                    {Object.entries(s.attributes ?? {}).map(([a, v]) => (
                      <Row key={a} k={a} v={typeof v === "object" ? JSON.stringify(v) : String(v)} />
                    ))}
                  </dl>
                  <div className="space-y-2">
                    {Object.keys(s.input_redacted ?? {}).length > 0 && <Json label="input (redacted)" v={s.input_redacted} />}
                    {Object.keys(s.output_redacted ?? {}).length > 0 && <Json label="output (redacted)" v={s.output_redacted} />}
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-muted">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </div>
  );
}

function Json({ label, v }: { label: string; v: unknown }) {
  return (
    <div>
      <p className="label mb-1 !text-[0.58rem]">{label}</p>
      <pre className="scrollbar-thin max-h-56 overflow-auto rounded-lg bg-[var(--code-bg)] p-2.5 font-mono text-[0.68rem] leading-relaxed text-[var(--code-ink)]">{JSON.stringify(v, null, 2)}</pre>
    </div>
  );
}
