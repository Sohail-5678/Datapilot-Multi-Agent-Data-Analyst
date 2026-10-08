"use client";

import { AlertTriangle, ArrowRight, CheckCircle2, FileSpreadsheet, Loader2, ShieldCheck, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DatabaseInfo } from "@/lib/types";
import { cn, fmtInt, pad2 } from "@/lib/utils";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.json,.sqlite,.sqlite3,.db";
const MAX_FILE = 10 * 1024 * 1024;
const MAX_TOTAL = 25 * 1024 * 1024;

function fmtBytes(n: number) {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

type Phase = { kind: "idle" } | { kind: "uploading"; pct: number; waking: boolean } | { kind: "processing" } | { kind: "done"; ds: DatabaseInfo } | { kind: "error"; message: string };

/** Upload straight to the API with a short-lived, upload-only token (XHR for real progress). */
async function uploadFiles(files: File[], title: string, onProgress: (pct: number) => void): Promise<DatabaseInfo> {
  const tok = await fetch("/api/upload-token", { cache: "no-store" });
  const t = (await tok.json().catch(() => ({}))) as { url?: string; token?: string; error?: { message: string } };
  if (!tok.ok || !t.url || !t.token) throw new Error(t.error?.message ?? "Couldn't start the upload.");
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.append("title", title);
  form.append("consent", "true");
  return new Promise<DatabaseInfo>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", t.url!);
    xhr.setRequestHeader("Authorization", `Bearer ${t.token}`);
    xhr.timeout = 180_000;
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: { error?: { message?: string } } & Partial<DatabaseInfo> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* HTML error page while the free server wakes */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as DatabaseInfo);
      else if ([502, 503, 504].includes(xhr.status)) reject(new Error("The free server is waking up — try again in a minute."));
      else reject(new Error(body.error?.message ?? `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error("Network error — the server may be waking up. Try again in a minute."));
    xhr.ontimeout = () => reject(new Error("The upload timed out. Try a smaller file."));
    xhr.send(form);
  });
}

export function UploadDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (ds: DatabaseInfo) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState("");
  const [consent, setConsent] = useState(false);
  const [drag, setDrag] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && phase.kind !== "uploading" && phase.kind !== "processing" && onClose();
    window.addEventListener("keydown", onKey);
    dialog.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, phase.kind]);

  if (!open) return null;
  const total = files.reduce((a, f) => a + f.size, 0);
  const tooBig = files.find((f) => f.size > MAX_FILE);
  const problem = tooBig ? `${tooBig.name} is larger than 10 MB.` : total > MAX_TOTAL ? "Files total more than 25 MB." : files.length > 10 ? "At most 10 files at a time." : null;
  const busy = phase.kind === "uploading" || phase.kind === "processing";

  const add = (list: FileList | null) => {
    if (!list) return;
    const next = [...files];
    for (const f of Array.from(list)) if (!next.some((x) => x.name === f.name && x.size === f.size)) next.push(f);
    setFiles(next.slice(0, 10));
    if (!title && list[0]) setTitle(list[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 80));
    setPhase({ kind: "idle" });
  };

  const submit = async () => {
    if (!files.length || !consent || problem) return;
    setPhase({ kind: "uploading", pct: 0, waking: false });
    const wakeTimer = setTimeout(() => setPhase((p) => (p.kind === "uploading" && p.pct >= 100 ? { kind: "processing" } : p.kind === "uploading" ? { ...p, waking: true } : p)), 8000);
    try {
      const ds = await uploadFiles(files, title, (pct) => setPhase(pct >= 100 ? { kind: "processing" } : { kind: "uploading", pct, waking: false }));
      setPhase({ kind: "done", ds });
    } catch (e) {
      setPhase({ kind: "error", message: e instanceof Error ? e.message : "Upload failed." });
    } finally {
      clearTimeout(wakeTimer);
    }
  };

  const reset = () => {
    setFiles([]);
    setTitle("");
    setConsent(false);
    setPhase({ kind: "idle" });
  };

  return (
    <div className="fixed inset-0 z-[60] grid place-items-center p-4" role="presentation">
      <button type="button" className="absolute inset-0 bg-navy-ink/60 backdrop-blur-sm" aria-label="Close" onClick={() => !busy && onClose()} />
      <div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="upload-title" className="card relative z-10 max-h-[92dvh] w-full max-w-2xl overflow-y-auto p-6 outline-none sm:p-8">
        <button type="button" onClick={() => !busy && onClose()} className="absolute right-5 top-5 grid size-9 place-items-center rounded-full hover:bg-surface-2" aria-label="Close">
          <X className="size-4" />
        </button>

        {phase.kind === "done" ? (
          <div>
            <p className="eyebrow text-accent">Ready</p>
            <h2 id="upload-title" className="display mt-2 text-4xl">{phase.ds.title}</h2>
            <p className="mt-2 text-sm text-muted">
              {phase.ds.tables} table{phase.ds.tables === 1 ? "" : "s"} · {fmtInt(phase.ds.rows)} rows · private to you
              {phase.ds.expires_at ? ` · kept until ${new Date(phase.ds.expires_at).toLocaleDateString()}` : ""}
            </p>
            <ol className="mt-6 space-y-3">
              {(phase.ds.table_details ?? []).map((t, i) => (
                <li key={t.name} className="card-flat p-4">
                  <div className="flex items-baseline gap-3">
                    <span className="font-serif text-lg italic text-accent">{pad2(i + 1)}</span>
                    <span className="font-serif text-xl font-semibold">{t.name}</span>
                    <span className="ml-auto font-mono text-[0.62rem] uppercase tracking-[0.14em] text-muted">{fmtInt(t.rows)} rows · {t.source}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {t.columns.map((c) => (
                      <span key={c.name} className={cn("rounded-md px-1.5 py-0.5 font-mono text-[0.66rem]", c.pii ? "bg-warn-soft text-warn" : "bg-surface-sunk text-ink-2")} title={`Header: ${c.original}`}>
                        {c.name} <span className="opacity-60">{c.type.toLowerCase()}</span>
                        {c.pii && " · masked"}
                      </span>
                    ))}
                  </div>
                  {t.truncated && <p className="mt-2 text-xs text-warn">Only the first 200,000 rows were kept.</p>}
                </li>
              ))}
            </ol>
            {!!phase.ds.pii_columns?.length && (
              <p className="mt-4 flex items-start gap-2 text-xs text-muted">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-positive" />
                Columns that look personal ({phase.ds.pii_columns.join(", ")}) are masked in samples and kept out of prompts. They still show in your result tables.
              </p>
            )}
            <div className="mt-7 flex flex-wrap gap-3">
              <button type="button" className="btn btn-crimson" onClick={() => { onDone(phase.ds); reset(); }}>
                Start asking <ArrowRight className="size-4" />
              </button>
              <button type="button" className="btn btn-ghost" onClick={reset}>Upload another</button>
            </div>
          </div>
        ) : (
          <div>
            <p className="eyebrow text-accent">Your data</p>
            <h2 id="upload-title" className="display mt-2 text-4xl">Ask questions about your own files.</h2>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              CSV, TSV, Excel (every sheet becomes a table), JSON or SQLite — up to 10 MB per file, 25 MB per dataset. Upload several files to join
              them. The agents treat them exactly like the demo databases: read-only, guarded, every number checked.
            </p>

            <div
              role="button"
              tabIndex={0}
              onClick={() => input.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                add(e.dataTransfer.files);
              }}
              className={cn(
                "mt-6 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition",
                drag ? "border-accent bg-accent-soft" : "border-line-strong hover:border-ink hover:bg-surface-2",
              )}
              aria-label="Choose files to upload"
            >
              <Upload className="size-6 text-accent" />
              <p className="font-serif text-xl">Drop files here, or click to choose</p>
              <p className="font-mono text-[0.62rem] uppercase tracking-[0.18em] text-muted">.csv · .tsv · .xlsx · .json · .sqlite / .db</p>
              <input ref={input} type="file" multiple accept={ACCEPT} className="sr-only" onChange={(e) => add(e.target.files)} />
            </div>

            {files.length > 0 && (
              <ul className="mt-4 space-y-1.5">
                {files.map((f) => (
                  <li key={f.name + f.size} className="flex items-center gap-3 rounded-xl bg-surface-2 px-3 py-2 text-sm">
                    <FileSpreadsheet className="size-4 shrink-0 text-muted" />
                    <span className="truncate">{f.name}</span>
                    <span className={cn("ml-auto shrink-0 font-mono text-xs", f.size > MAX_FILE ? "text-accent-ink" : "text-muted")}>{fmtBytes(f.size)}</span>
                    <button type="button" disabled={busy} onClick={() => setFiles(files.filter((x) => x !== f))} className="grid size-7 place-items-center rounded-full hover:bg-surface-sunk" aria-label={`Remove ${f.name}`}>
                      <X className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <label htmlFor="ds-title" className="label mt-6 block">Name this dataset</label>
            <input id="ds-title" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} className="input mt-2" placeholder="e.g. Q3 sales" />

            <label className="mt-6 flex cursor-pointer items-start gap-3 rounded-2xl border border-line p-4 text-sm">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-1 size-4 accent-[var(--accent)]" />
              <span className="leading-relaxed text-ink-2">
                I understand that column names, a few sample values and query results are sent to <b>free-tier AI providers (Google Gemini, Groq)</b>,
                which may use them to improve their models. I won&apos;t upload confidential or personal data. Datasets are private to me and are
                deleted automatically (guests: 1 day, GitHub users: 7 days).
              </span>
            </label>

            {(problem || phase.kind === "error") && (
              <p className="mt-4 flex items-start gap-2 rounded-xl bg-accent-soft px-4 py-3 text-sm text-accent-ink" role="alert">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {problem ?? (phase.kind === "error" ? phase.message : "")}
              </p>
            )}

            {busy && (
              <div className="mt-5" role="status" aria-live="polite">
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunk">
                  <div className={cn("h-full rounded-full bg-accent transition-[width] duration-300", phase.kind === "processing" && "w-full animate-pulse")} style={phase.kind === "uploading" ? { width: `${Math.max(4, phase.pct)}%` } : undefined} />
                </div>
                <p className="mt-2 text-xs text-muted">
                  {phase.kind === "processing"
                    ? "Reading the files, inferring column types and building your read-only database…"
                    : phase.waking
                      ? "Waking up the free server — this can take up to a minute…"
                      : `Uploading… ${phase.pct}%`}
                </p>
              </div>
            )}

            <div className="mt-7 flex flex-wrap items-center gap-3">
              <button type="button" onClick={submit} disabled={!files.length || !consent || !!problem || busy} className="btn btn-crimson">
                {busy ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />} Upload & analyze
              </button>
              <button type="button" onClick={onClose} disabled={busy} className="btn btn-ghost">Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
