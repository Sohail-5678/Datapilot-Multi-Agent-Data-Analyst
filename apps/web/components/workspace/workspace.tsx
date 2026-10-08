"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUp, ChevronRight, Database, Hash, KeyRound, Link2, Loader2, MessageSquarePlus, PanelLeft, Sparkles, Type, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { TurnView } from "@/components/workspace/turn-view";
import { api, ApiError } from "@/lib/api";
import type { DatabaseInfo, SchemaInfo, ThreadInfo } from "@/lib/types";
import { useConversation } from "@/lib/use-conversation";
import { cn, fmtInt, pad2, timeAgo } from "@/lib/utils";
import { getSandbox } from "@/sandbox/runner";

const EMPTY_COPY = "Ask a question in plain English. DataPilot plans it, writes and checks the SQL, and shows you the answer with a chart.";

function useBackendHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const r = await fetch("/api/health", { cache: "no-store" });
      const d = (await r.json().catch(() => ({}))) as { status?: string; indexes_ready?: boolean };
      return { ok: r.ok && d.status === "ok", status: d.status ?? "starting", ready: d.indexes_ready !== false };
    },
    refetchInterval: (q) => (q.state.data?.ok && q.state.data.ready ? false : 4000),
    staleTime: 30_000,
  });
}

function WakingBanner() {
  const h = useBackendHealth();
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (h.data?.ok) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [h.data?.ok]);
  if (!h.data || (h.data.ok && h.data.ready)) return null;
  const unconfigured = h.data.status === "unconfigured";
  return (
    <div className="mb-6 flex items-center gap-3 rounded-2xl border border-line bg-surface-2 px-4 py-3 text-sm" role="status" aria-live="polite">
      <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
      <span>
        {unconfigured
          ? "The API server isn't connected yet. You can still browse the landing page and benchmarks."
          : `Waking up the free server — it sleeps after 15 idle minutes and takes up to a minute to start. ${elapsed ? `(${elapsed} s)` : ""}`}
      </span>
    </div>
  );
}

function SchemaTree({ schema }: { schema: SchemaInfo | undefined }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!schema) return <div className="dp-shimmer h-48 rounded-xl" />;
  return (
    <ul className="space-y-0.5">
      {schema.tables.map((t) => (
        <li key={t.name}>
          <button type="button" onClick={() => setOpen(open === t.name ? null : t.name)} aria-expanded={open === t.name} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[0.82rem] hover:bg-surface-2">
            <ChevronRight className={cn("size-3.5 shrink-0 text-muted transition-transform", open === t.name && "rotate-90")} />
            <span className="truncate font-medium">{t.name}</span>
            <span className="ml-auto font-mono text-[0.6rem] text-muted">{fmtInt(t.rows)}</span>
          </button>
          {open === t.name && (
            <ul className="mb-2 ml-5 border-l border-line pl-3">
              {t.columns.map((c) => {
                const Icon = c.pk ? KeyRound : c.fk ? Link2 : /INT|REAL|NUM|DEC|FLOAT|DOUBLE/i.test(c.type) ? Hash : Type;
                return (
                  <li key={c.name} className="flex items-center gap-2 py-0.5 text-[0.76rem] text-ink-2" title={c.description || c.name}>
                    <Icon className={cn("size-3 shrink-0", c.pk ? "text-accent" : "text-muted")} aria-label={c.pk ? "primary key" : c.fk ? "foreign key" : c.type} />
                    <span className="truncate">{c.name}</span>
                    {c.pii && <span className="rounded bg-warn-soft px-1 font-mono text-[0.55rem] text-warn">PII</span>}
                    <span className="ml-auto shrink-0 font-mono text-[0.58rem] uppercase text-muted">{(c.type || "any").split("(")[0].toLowerCase()}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Sidebar({ dbs, dbId, onPickDb, schema, onExample, threads, activeThread, onClose }: {
  dbs: DatabaseInfo[] | undefined;
  dbId: string;
  onPickDb: (id: string) => void;
  schema: SchemaInfo | undefined;
  onExample: (q: string) => void;
  threads: ThreadInfo[] | undefined;
  activeThread: string | null;
  onClose?: () => void;
}) {
  const db = dbs?.find((d) => d.db_id === dbId);
  return (
    <div className="flex h-full flex-col gap-7 overflow-y-auto scrollbar-thin p-5">
      {onClose && (
        <button type="button" onClick={onClose} className="ml-auto grid size-9 place-items-center rounded-full border border-line-strong" aria-label="Close schema panel">
          <X className="size-4" />
        </button>
      )}
      <div>
        <label htmlFor="db-pick" className="label">Database</label>
        <div className="relative mt-2">
          <select id="db-pick" value={dbId} onChange={(e) => onPickDb(e.target.value)} className="input appearance-none !rounded-xl !py-2.5 pr-9 font-serif !text-lg font-semibold">
            {(dbs ?? [{ db_id: dbId, title: dbId } as DatabaseInfo]).map((d) => (
              <option key={d.db_id} value={d.db_id}>{d.title}</option>
            ))}
          </select>
          <Database className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
        </div>
        {db && (
          <p className="mt-2 text-xs leading-relaxed text-muted">
            {db.subtitle} · {db.tables} tables · {fmtInt(db.rows)} rows ·{" "}
            <Link href={`/databases/${db.db_id}`} className="underline underline-offset-2 hover:text-ink">docs</Link>
          </p>
        )}
      </div>
      <div>
        <p className="label mb-2">Tables</p>
        <SchemaTree schema={schema} />
      </div>
      <div>
        <p className="label mb-2">Examples</p>
        <ul className="space-y-1">
          {(db?.examples ?? []).map((q, i) => (
            <li key={q}>
              <button type="button" onClick={() => onExample(q)} className="flex w-full gap-2.5 rounded-lg px-2 py-1.5 text-left text-[0.82rem] leading-snug hover:bg-surface-2">
                <span className="font-mono text-[0.62rem] text-accent">{pad2(i + 1)}</span> {q}
              </button>
            </li>
          ))}
        </ul>
      </div>
      {!!threads?.length && (
        <div>
          <p className="label mb-2">Recent</p>
          <ul className="space-y-0.5">
            {threads.slice(0, 12).map((t) => (
              <li key={t.id}>
                <Link href={`/app/${t.id}`} aria-current={t.id === activeThread ? "page" : undefined} className={cn("block rounded-lg px-2 py-1.5 text-[0.8rem] hover:bg-surface-2", t.id === activeThread && "bg-surface-2 font-semibold")}>
                  <span className="line-clamp-1">{t.title || "Untitled conversation"}</span>
                  <span className="font-mono text-[0.58rem] uppercase tracking-[0.12em] text-muted">{t.db_id} · {timeAgo(t.updated_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Workspace({ initialDb, initialQuestion, threadId }: { initialDb: string; initialQuestion?: string | null; threadId?: string | null }) {
  const router = useRouter();
  const [dbId, setDbId] = useState(initialDb);
  const conv = useConversation(dbId, threadId);
  const effectiveDb = conv.threadDb ?? dbId;
  const [draft, setDraft] = useState("");
  const [drawer, setDrawer] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const askedInitial = useRef(false);
  const dbs = useQuery({ queryKey: ["dbs"], queryFn: () => api<{ databases: DatabaseInfo[] }>("databases").then((r) => r.databases), retry: 2 });
  const schema = useQuery({ queryKey: ["schema", effectiveDb], queryFn: () => api<SchemaInfo>(`databases/${effectiveDb}/schema`) });
  const threads = useQuery({ queryKey: ["threads", conv.threadId, conv.turns.length], queryFn: () => api<{ threads: ThreadInfo[] }>("threads").then((r) => r.threads) });
  const health = useBackendHealth();

  // Preload the Python sandbox once the page is idle (SPEC §8.1).
  useEffect(() => {
    const idle = (window as Window & { requestIdleCallback?: (cb: () => void) => number }).requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 2500));
    idle(() => void getSandbox().preload().catch(() => {}));
  }, []);

  useEffect(() => {
    if (initialQuestion && !threadId && !askedInitial.current && health.data?.ok) {
      askedInitial.current = true;
      window.history.replaceState(null, "", "/app");
      void conv.ask(initialQuestion);
    }
  }, [initialQuestion, threadId, health.data?.ok, conv]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conv.turns.length]);

  const db = useMemo(() => dbs.data?.find((d) => d.db_id === effectiveDb), [dbs.data, effectiveDb]);
  const submit = (q: string) => {
    if (!q.trim() || conv.busy) return;
    setDraft("");
    setDrawer(false);
    void conv.ask(q);
  };
  const pickDb = (id: string) => {
    setDrawer(false);
    if (conv.threadId) router.push(`/app?db=${id}`);
    else setDbId(id);
  };
  const sidebar = (onClose?: () => void) => (
    <Sidebar dbs={dbs.data} dbId={effectiveDb} onPickDb={pickDb} schema={schema.data} onExample={submit} threads={threads.data} activeThread={conv.threadId} onClose={onClose} />
  );

  return (
    <div className="relative z-10 mx-auto flex max-w-[1440px] gap-0 lg:gap-8 lg:px-8">
      <aside className="sticky top-0 hidden h-[calc(100dvh-5rem)] w-[300px] shrink-0 border-r border-line lg:block" aria-label="Database and schema">
        {sidebar()}
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Database and schema">
          <button type="button" className="absolute inset-0 bg-navy-ink/50 backdrop-blur-sm" onClick={() => setDrawer(false)} aria-label="Close" />
          <div className="absolute inset-y-0 left-0 w-[86%] max-w-sm bg-bg shadow-[var(--shadow-lift)]">{sidebar(() => setDrawer(false))}</div>
        </div>
      )}

      <main id="main" className="min-w-0 flex-1 px-4 pb-44 pt-6 sm:px-6 lg:px-2">
        <div className="mb-8 flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => setDrawer(true)} className="btn btn-ghost !px-3 !py-2 text-xs lg:hidden">
            <PanelLeft className="size-4" /> Schema
          </button>
          <div className="min-w-0">
            <p className="label">{db?.subtitle ?? "Database"}</p>
            <h1 className="display truncate text-3xl sm:text-4xl">{db?.title ?? effectiveDb}</h1>
          </div>
          {(conv.threadId || conv.turns.length > 0) && (
            <Link href={`/app?db=${effectiveDb}`} className="btn btn-ghost ml-auto !py-2 text-xs">
              <MessageSquarePlus className="size-4" /> New conversation
            </Link>
          )}
        </div>
        <WakingBanner />

        {conv.loading ? (
          <div className="space-y-4">
            <div className="dp-shimmer h-10 w-2/3 rounded-xl" />
            <div className="dp-shimmer h-56 rounded-2xl" />
          </div>
        ) : conv.loadError ? (
          <div className="card p-8 text-center" role="alert">
            <p className="font-serif text-2xl">{conv.loadError.status === 404 ? "Conversation not found" : "Couldn't load this conversation"}</p>
            <p className="mt-2 text-sm text-muted">{conv.loadError instanceof ApiError ? conv.loadError.message : ""}</p>
            <Link href="/app" className="btn btn-primary mt-6">Start a new one</Link>
          </div>
        ) : conv.turns.length === 0 ? (
          <div className="mx-auto max-w-2xl py-10 text-center sm:py-16">
            <Sparkles className="mx-auto size-6 text-accent" />
            <p className="display mx-auto mt-5 max-w-xl text-[2rem] leading-tight sm:text-[2.4rem]">{EMPTY_COPY}</p>
            <div className="mt-10 grid gap-3 text-left sm:grid-cols-2">
              {(db?.examples ?? []).map((q, i) => (
                <button key={q} type="button" onClick={() => submit(q)} disabled={!health.data?.ok} className="card group flex items-start gap-3 p-4 text-[0.92rem] leading-snug transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] disabled:opacity-60">
                  <span className="font-serif text-lg italic text-accent">{pad2(i + 1)}</span>
                  <span>{q}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-14">
            {conv.turns.map((t, i) => (
              <TurnView
                key={t.key}
                turn={t}
                dbId={effectiveDb}
                index={i}
                onClarify={(c) => conv.answerClarify(t, c)}
                onConfirm={(a) => conv.answerConfirm(t, a)}
                onFeedback={(v) => conv.sendFeedback(t, v)}
                onRunAgain={() => submit(t.question)}
              />
            ))}
          </div>
        )}
        <div ref={bottom} />
      </main>

      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 bg-gradient-to-t from-bg via-bg/95 to-transparent pb-5 pt-10 lg:left-[332px]">
        <form
          className="pointer-events-auto mx-auto flex max-w-3xl items-end gap-2 px-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft);
          }}
        >
          <label htmlFor="ask" className="sr-only">Ask a question about this database</label>
          <div className="card flex flex-1 items-end gap-2 !rounded-[1.6rem] p-2 pl-5">
            <textarea
              id="ask"
              rows={1}
              value={draft}
              maxLength={1000}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(draft);
                }
              }}
              placeholder={`Ask a question about ${db?.title ?? "this database"}…`}
              className="max-h-40 min-h-[2.6rem] flex-1 resize-none bg-transparent py-2.5 text-[0.98rem] outline-none placeholder:text-muted"
            />
            <button type="submit" disabled={!draft.trim() || conv.busy || !health.data?.ok} className="btn btn-crimson !size-11 !p-0" aria-label="Ask">
              {conv.busy ? <Loader2 className="size-5 animate-spin" /> : <ArrowUp className="size-5" />}
            </button>
          </div>
        </form>
        <p className="pointer-events-none mt-2 text-center font-mono text-[0.58rem] uppercase tracking-[0.2em] text-muted">Read-only · demo data · every number checked</p>
      </div>
    </div>
  );
}
