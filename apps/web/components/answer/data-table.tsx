"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { CalendarDays, Check, ClipboardCopy, Hash, Type } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { cn, fmtCell, fmtInt, toCsv } from "@/lib/utils";

type Kind = "number" | "date" | "text";

function kindOf(rows: unknown[][], i: number): Kind {
  const vals = rows.slice(0, 100).map((r) => r[i]).filter((v) => v !== null && v !== undefined);
  if (vals.length && vals.every((v) => typeof v === "number")) return "number";
  if (vals.length && vals.every((v) => typeof v === "string" && /^\d{4}-\d{2}(-\d{2})?/.test(v))) return "date";
  return "text";
}

const ICON = { number: Hash, date: CalendarDays, text: Type };
const ROW_H = 38;

/** Virtualized result table: sticky header, column type icons, copy as CSV, 1,000-row display cap note. */
export function DataTable({ columns, rows, rowCount, truncated, maxHeight = 420 }: { columns: string[]; rows: unknown[][]; rowCount: number; truncated?: boolean; maxHeight?: number }) {
  const parent = useRef<HTMLDivElement>(null);
  const kinds = useMemo(() => columns.map((_, i) => kindOf(rows, i)), [columns, rows]);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => ROW_H, overscan: 12 });
  const [copied, setCopied] = useState(false);
  const widths = useMemo(
    () =>
      columns.map((c, i) => {
        const sample = rows.slice(0, 50).map((r) => fmtCell(r[i]).length);
        const len = Math.max(c.length + 3, ...sample);
        return Math.min(320, Math.max(96, len * 8.4 + 32));
      }),
    [columns, rows],
  );
  const total = widths.reduce((a, b) => a + b, 0);
  const copy = async () => {
    await navigator.clipboard.writeText(toCsv(columns, rows));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  if (!columns.length) return <p className="text-sm text-muted">No columns returned.</p>;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="label">
          {fmtInt(rowCount)} row{rowCount === 1 ? "" : "s"} · {columns.length} column{columns.length === 1 ? "" : "s"}
        </p>
        <button type="button" onClick={copy} className="chip hover:bg-surface-2">
          {copied ? <Check className="size-3.5" /> : <ClipboardCopy className="size-3.5" />} {copied ? "Copied" : "Copy as CSV"}
        </button>
      </div>
      <div ref={parent} className="scrollbar-thin overflow-auto rounded-xl border border-line" style={{ maxHeight }} role="table" aria-label="Query results" aria-rowcount={rows.length + 1}>
        <div style={{ width: Math.max(total, 0), minWidth: "100%" }}>
          <div role="row" className="sticky top-0 z-10 flex border-b border-line-strong bg-surface-2/95 backdrop-blur" style={{ height: ROW_H + 2 }}>
            {columns.map((c, i) => {
              const Icon = ICON[kinds[i]];
              return (
                <div key={c + i} role="columnheader" className={cn("flex items-center gap-1.5 px-3 text-xs font-bold", kinds[i] === "number" && "justify-end")} style={{ width: widths[i], flex: `1 0 ${widths[i]}px` }} title={c}>
                  <Icon className="size-3 shrink-0 text-muted" aria-label={kinds[i]} />
                  <span className="truncate">{c}</span>
                </div>
              );
            })}
          </div>
          <div style={{ height: v.getTotalSize(), position: "relative" }}>
            {v.getVirtualItems().map((item) => {
              const r = rows[item.index];
              return (
                <div key={item.key} role="row" className={cn("absolute left-0 flex w-full border-b border-line/70 text-[0.8125rem]", item.index % 2 ? "bg-surface-2/40" : "")} style={{ height: ROW_H, transform: `translateY(${item.start}px)` }}>
                  {columns.map((c, i) => (
                    <div key={c + i} role="cell" className={cn("flex items-center truncate px-3", kinds[i] === "number" ? "justify-end font-mono tabular-nums" : "", r[i] === null && "text-muted italic")} style={{ width: widths[i], flex: `1 0 ${widths[i]}px` }} title={fmtCell(r[i])}>
                      <span className="truncate">{fmtCell(r[i])}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {(truncated || rowCount > rows.length) && (
        <p className="mt-2 text-xs text-muted">Showing the first {fmtInt(rows.length)} of {fmtInt(rowCount)} rows. Copy or edit the SQL to narrow it down.</p>
      )}
    </div>
  );
}
