"use client";

import { AlertTriangle, BadgeCheck, Cpu, ExternalLink, Pencil, RotateCcw, ShieldAlert, ThumbsDown, ThumbsUp } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";
import { DataTable } from "@/components/answer/data-table";
import { RichText } from "@/components/answer/rich-text";
import { SqlEditor } from "@/components/answer/sql-editor";
import { VegaChart } from "@/components/answer/vega-chart";
import { TraceTimeline } from "@/components/trace/trace-timeline";
import { CodeBlock } from "@/components/ui/code";
import { panelId, tabId, Tabs, type TabDef } from "@/components/ui/tabs";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { describeChart, validateChartSpec } from "@/lib/chart-spec";
import type { Turn } from "@/lib/turn";
import type { RunRecord, TableResult } from "@/lib/types";
import { cn, fmtMs, fmtUsd } from "@/lib/utils";

const COPY_LOW = "The agents didn't fully agree on this one. Check the SQL tab before relying on it.";
const COPY_REMOVED = "Part of the draft answer wasn't supported by the data, so it was removed.";

export function ConfidenceBadge({ confidence, reasons }: { confidence: string | null; reasons?: string[] }) {
  if (!confidence) return null;
  const tone =
    confidence === "High"
      ? "bg-navy text-ivory dark:bg-ivory dark:text-navy"
      : confidence === "Medium"
        ? "bg-stone/50 text-ink"
        : "bg-accent-soft text-accent-ink ring-1 ring-accent/40";
  const Icon = confidence === "Low" ? AlertTriangle : BadgeCheck;
  return (
    <Tooltip
      content={
        <span className="block space-y-1">
          <span className="block font-semibold">Confidence: {confidence}</span>
          <span className="block text-muted">High = 2+ candidates agree and the verifier passed · Medium = 1 candidate + verifier pass · Low = otherwise.</span>
          {reasons?.map((r) => (
            <span key={r} className="block">✦ {r}</span>
          ))}
        </span>
      }
    >
      <span className={cn("chip border-transparent !py-1", tone)}>
        <Icon className="size-3.5" /> {confidence}
      </span>
    </Tooltip>
  );
}

/** Stored runs keep only metadata in the turn; rows are fetched lazily the first time a view needs them. */
function useTable(runId: string | null, t: TableResult | undefined) {
  const [fetched, setFetched] = useState<Record<string, TableResult>>({});
  useEffect(() => {
    if (!runId || !t || t.rows.length || !t.row_count) return;
    let alive = true;
    api<{ columns: string[]; rows: unknown[][]; total_rows: number }>(`runs/${runId}/data/${t.data_ref}`)
      .then((d) => alive && setFetched((f) => ({ ...f, [t.data_ref]: { ...t, columns: d.columns, rows: d.rows, row_count: d.total_rows ?? t.row_count } })))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [runId, t]);
  return t && !t.rows.length && fetched[t.data_ref] ? fetched[t.data_ref] : t;
}

export function AnswerCard({
  turn,
  dbId,
  onFeedback,
  onRunAgain,
}: {
  turn: Turn;
  dbId: string;
  onFeedback: (v: 1 | -1) => void;
  onRunAgain: () => void;
}) {
  const [tab, setTab] = useState("answer");
  const base = useId();
  const [editing, setEditing] = useState(false);
  const [trace, setTrace] = useState<RunRecord | null>(null);
  const lastTable = turn.tables[turn.tables.length - 1];
  const chartSrc = turn.chart ? turn.tables.find((t) => t.data_ref === turn.chart?.data_ref) : undefined;
  const table = useTable(turn.runId, lastTable);
  const chartTable = useTable(turn.runId, chartSrc);
  const chosen = turn.chosen[turn.chosen.length - 1];
  const sqls = turn.chosen;
  const chartSpec = useMemo(() => (turn.chart && chartTable ? validateChartSpec(turn.chart.spec, chartTable.columns) : null), [turn.chart, chartTable]);
  const done = turn.phase === "done";

  useEffect(() => {
    if (tab !== "trace" || !turn.runId || !done) return;
    let alive = true;
    api<RunRecord>(`runs/${turn.runId}`).then((r) => alive && setTrace(r)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [tab, turn.runId, done]);

  const tabs: TabDef[] = [{ id: "answer", label: "Answer" }];
  if (table) tabs.push({ id: "table", label: "Table", badge: table.row_count });
  if (chartSpec) tabs.push({ id: "chart", label: "Chart" });
  if (sqls.length) tabs.push({ id: "sql", label: "SQL" });
  if (turn.analysis?.code) tabs.push({ id: "python", label: "Python" });
  if (turn.runId) tabs.push({ id: "trace", label: "Trace" });

  const removed = turn.grounding?.removed?.length ?? 0;
  const lowConfidence = turn.confidence === "Low";
  return (
    <article className="card overflow-hidden" aria-label="Answer">
      <header className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3.5 sm:px-6">
        <Tabs base={base} tabs={tabs} value={tab} onChange={setTab} className="min-w-0 flex-1" />
        <div className="flex items-center gap-2">
          {turn.fake && <span className="chip !border-dashed !text-[0.65rem] text-muted">offline fake model</span>}
          <ConfidenceBadge confidence={turn.confidence} reasons={chosen?.reasons} />
        </div>
      </header>

      <div className="px-5 py-6 sm:px-7" role="tabpanel" id={panelId(base, tab)} aria-labelledby={tabId(base, tab)} tabIndex={0}>
        {tab === "answer" && (
          <div>
            {turn.answer ? (
              <RichText text={turn.answer} className="prose-answer font-serif text-[1.32rem] leading-[1.5] text-ink" />
            ) : (
              <p className="font-serif text-xl text-muted">{done ? "No answer text was produced." : "Writing the answer…"}</p>
            )}
            {(turn.notes ?? []).map((n) => (
              <p key={n} className="mt-3 text-sm text-muted">{n}</p>
            ))}
            {lowConfidence && done && (
              <p className="mt-5 flex items-start gap-2 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" /> <span><b>Please double-check this answer.</b> {COPY_LOW}</span>
              </p>
            )}
            <div className="mt-6 flex flex-wrap items-center gap-2">
              {turn.grounding && (removed ? (
                <span className="chip border-warn/40 bg-warn-soft text-warn"><ShieldAlert className="size-3.5" /> {COPY_REMOVED}</span>
              ) : (
                <span className="chip border-positive/30 bg-positive-soft text-positive">
                  <BadgeCheck className="size-3.5" /> Numbers checked against results ✓{turn.grounding.checked ? ` · ${turn.grounding.checked}` : ""}
                </span>
              ))}
              {turn.analysis?.ok && (
                <span className="chip border-line-strong text-ink-2">
                  <Cpu className="size-3.5" /> Ran in your browser · no network · {fmtMs(turn.analysis.duration_ms ?? 0)}
                </span>
              )}
              {turn.analysis && !turn.analysis.ok && <span className="chip border-warn/40 text-warn"><AlertTriangle className="size-3.5" /> Analysis step skipped</span>}
            </div>
            {chartSpec && chartTable && tab === "answer" && (
              <figure className="mt-7 rounded-2xl border border-line bg-surface-2/50 p-4">
                <VegaChart spec={chartSpec} columns={chartTable.columns} rows={chartTable.rows} className="w-full" />
                <figcaption className="sr-only">{describeChart(chartSpec, chartTable.columns, chartTable.rows)}</figcaption>
              </figure>
            )}
          </div>
        )}
        {tab === "table" && table && <DataTable columns={table.columns} rows={table.rows} rowCount={table.row_count} truncated={table.truncated} />}
        {tab === "chart" && chartSpec && chartTable && (
          <figure>
            <VegaChart spec={chartSpec} columns={chartTable.columns} rows={chartTable.rows} className="w-full" />
            <figcaption className="mt-3 text-xs text-muted">{describeChart(chartSpec, chartTable.columns, chartTable.rows)} The table tab has the same data.</figcaption>
          </figure>
        )}
        {tab === "sql" && (
          <div className="space-y-5">
            {sqls.map((c) => (
              <div key={c.step_idx}>
                {sqls.length > 1 && <p className="label mb-2">Step {c.step_idx + 1}</p>}
                <CodeBlock code={c.sql} lang="sql" label={`SQL · ${c.agree} of ${c.of} candidates agree`} />
                <ul className="mt-3 flex flex-wrap gap-2">
                  {c.reasons.map((r) => (
                    <li key={r} className="chip text-muted">{r}</li>
                  ))}
                </ul>
              </div>
            ))}
            {editing && chosen ? (
              <SqlEditor dbId={dbId} initial={chosen.sql} onClose={() => setEditing(false)} />
            ) : (
              <button type="button" className="btn btn-ghost" onClick={() => setEditing(true)}>
                <Pencil className="size-4" /> Edit SQL
              </button>
            )}
          </div>
        )}
        {tab === "python" && turn.analysis?.code && (
          <div className="space-y-4">
            <CodeBlock code={turn.analysis.code} lang="python" label="Python · Pyodide sandbox" />
            {turn.analysis.ok ? (
              <CodeBlock code={JSON.stringify(turn.analysis.result, null, 2)} lang="python" label="result" />
            ) : (
              <p className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">{turn.analysis.error}</p>
            )}
            {turn.analysis.stdout && <pre className="scrollbar-thin max-h-48 overflow-auto rounded-xl bg-surface-2 p-3 font-mono text-xs">{turn.analysis.stdout}</pre>}
            <p className="text-xs text-muted">
              Model-written Python never runs on the server. It ran in a WebAssembly sandbox in this browser tab, in a Web Worker with network access removed and a 10-second limit.
            </p>
          </div>
        )}
        {tab === "trace" && (
          <div>
            {!done ? (
              <p className="text-sm text-muted">The trace is available when the run finishes.</p>
            ) : trace?.spans ? (
              <TraceTimeline spans={trace.spans} compact />
            ) : (
              <div className="dp-shimmer h-40 rounded-xl" />
            )}
            {turn.runId && done && (
              <Link href={`/runs/${turn.runId}`} className="btn btn-ghost mt-5">
                Open the full trace <ExternalLink className="size-4" />
              </Link>
            )}
          </div>
        )}
      </div>

      {done && (
        <footer className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3 sm:px-6">
          <div className="flex items-center gap-1" role="group" aria-label="Was this helpful?">
            <button type="button" aria-pressed={turn.feedback === 1} onClick={() => onFeedback(1)} className={cn("grid size-9 place-items-center rounded-full transition hover:bg-surface-2", turn.feedback === 1 && "bg-navy text-ivory hover:bg-navy dark:bg-ivory dark:text-navy")} aria-label="Helpful">
              <ThumbsUp className="size-4" />
            </button>
            <button type="button" aria-pressed={turn.feedback === -1} onClick={() => onFeedback(-1)} className={cn("grid size-9 place-items-center rounded-full transition hover:bg-surface-2", turn.feedback === -1 && "bg-accent text-white hover:bg-accent")} aria-label="Not helpful">
              <ThumbsDown className="size-4" />
            </button>
          </div>
          <button type="button" onClick={onRunAgain} className="btn !px-3 !py-1.5 text-xs text-muted hover:bg-surface-2 hover:text-ink">
            <RotateCcw className="size-3.5" /> Run again
          </button>
          {chosen && (
            <button type="button" onClick={() => { setTab("sql"); setEditing(true); }} className="btn !px-3 !py-1.5 text-xs text-muted hover:bg-surface-2 hover:text-ink">
              <Pencil className="size-3.5" /> Edit SQL
            </button>
          )}
          {turn.metrics && (
            <p className="ml-auto font-mono text-[0.62rem] tracking-[0.12em] text-muted">
              {turn.metrics.llm_calls} LLM CALLS · {(turn.metrics.tokens_in + turn.metrics.tokens_out).toLocaleString()} TOKENS · {fmtMs(turn.metrics.latency_ms)} · LIST PRICE {fmtUsd(turn.metrics.list_price_cost_usd)}
            </p>
          )}
        </footer>
      )}
    </article>
  );
}
