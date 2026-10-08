"use client";

import { Loader2, Play, X } from "lucide-react";
import { useState } from "react";
import { DataTable } from "@/components/answer/data-table";
import { api, ApiError } from "@/lib/api";
import { fmtInt, fmtMs } from "@/lib/utils";

interface ExecOk {
  sql: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  duration_ms: number;
}
interface NeedsConfirm {
  needs_confirm: true;
  scanned_rows: number;
  table: string;
  seconds: string;
}

/** User-edited SQL goes through the same guard and runs read-only on the server (SPEC §2.4). */
export function SqlEditor({ dbId, initial, onClose }: { dbId: string; initial: string; onClose: () => void }) {
  const [sql, setSql] = useState(initial.replace(/\nLIMIT 1000$/, ""));
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ExecOk | null>(null);
  const [confirm, setConfirm] = useState<NeedsConfirm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (force = false) => {
    setBusy(true);
    setErr(null);
    setConfirm(null);
    try {
      const r = await api<ExecOk | NeedsConfirm>("sql/execute", { json: { db_id: dbId, sql, confirm: force } });
      if ("needs_confirm" in r) setConfirm(r);
      else setRes(r);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Query failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-2xl border border-line-strong bg-surface-2/60 p-4">
      <div className="mb-2 flex items-center justify-between">
        <label htmlFor="sql-edit" className="label">Edit SQL · runs read-only through the same guard</label>
        <button type="button" onClick={onClose} className="grid size-8 place-items-center rounded-full hover:bg-surface-sunk" aria-label="Close editor">
          <X className="size-4" />
        </button>
      </div>
      <textarea
        id="sql-edit"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void run();
        }}
        spellCheck={false}
        rows={Math.min(16, Math.max(5, sql.split("\n").length + 1))}
        className="input font-mono !text-[0.8125rem] leading-relaxed !bg-[var(--code-bg)] !text-[var(--code-ink)]"
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => run()} disabled={busy || !sql.trim()} className="btn btn-primary !py-2">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />} Run
        </button>
        <span className="text-xs text-muted">⌘/Ctrl + Enter</span>
        {res && <span className="label">{fmtInt(res.row_count)} rows · {fmtMs(res.duration_ms)}</span>}
      </div>
      {err && <p className="mt-3 rounded-xl bg-accent-soft px-3 py-2 text-sm text-accent-ink">{err}</p>}
      {confirm && (
        <div className="mt-3 rounded-xl border border-accent/40 bg-accent-soft p-3 text-sm">
          This will scan about <b>{fmtInt(confirm.scanned_rows)}</b> rows in <code>{confirm.table}</code> ({confirm.seconds}).{" "}
          <button type="button" className="font-semibold underline" onClick={() => run(true)}>Run anyway</button>
        </div>
      )}
      {res && (
        <div className="mt-4">
          <DataTable columns={res.columns} rows={res.rows} rowCount={res.row_count} truncated={res.truncated} maxHeight={320} />
        </div>
      )}
    </div>
  );
}
