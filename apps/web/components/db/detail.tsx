"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, KeyRound, Link2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Waking } from "@/components/db/catalog";
import { Erd } from "@/components/db/erd";
import { api, ApiError } from "@/lib/api";
import type { SchemaInfo } from "@/lib/types";
import { cn, fmtInt, pad2 } from "@/lib/utils";

export function DatabaseDetail({ dbId }: { dbId: string }) {
  const q = useQuery({
    queryKey: ["schema", dbId],
    queryFn: () => api<SchemaInfo>(`databases/${dbId}/schema`),
    retry: (n, e) => (e instanceof ApiError && e.code === "backend_starting" ? n < 20 : false),
    retryDelay: 3000,
  });
  const [table, setTable] = useState<string | null>(null);
  const current = useMemo(() => q.data?.tables.find((t) => t.name === table) ?? q.data?.tables[0], [q.data, table]);
  if (q.isPending) return <div className="dp-shimmer mt-6 h-96 rounded-2xl" />;
  if (q.isError) {
    if (q.error instanceof ApiError && q.error.status === 404) {
      return (
        <div className="py-20 text-center">
          <h1 className="display text-5xl">No such database.</h1>
          <Link href="/databases" className="btn btn-primary mt-8">All databases</Link>
        </div>
      );
    }
    return <Waking error={q.error} />;
  }
  const s = q.data;
  const totalRows = s.tables.reduce((a, t) => a + t.rows, 0);
  return (
    <div>
      <Link href="/databases" className="label hover:text-ink">← All databases</Link>
      <div className="mt-6 grid gap-8 lg:grid-cols-[1.2fr_1fr] lg:items-end">
        <div>
          <h1 className="display text-5xl sm:text-6xl">{s.title}</h1>
          <p className="mt-4 max-w-2xl text-[0.98rem] leading-relaxed text-muted">{s.description}</p>
          <p className="mt-4 font-mono text-[0.62rem] uppercase tracking-[0.2em] text-muted">
            {s.tables.length} tables · {fmtInt(totalRows)} rows · {s.source} · {s.license}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end">
          {s.examples.slice(0, 2).map((e) => (
            <Link key={e} href={`/app?db=${s.db_id}&q=${encodeURIComponent(e)}`} className="chip max-w-full hover:bg-surface-2">
              <span className="truncate">Ask: {e}</span> <ArrowRight className="size-3.5 shrink-0" />
            </Link>
          ))}
        </div>
      </div>

      <section className="card mt-10 overflow-hidden" aria-labelledby="erd">
        <h2 id="erd" className="label px-6 pt-5">Entity relationships</h2>
        <Erd tables={s.tables} erd={s.erd} onPick={setTable} active={current?.name ?? null} />
      </section>

      <section className="mt-10 grid gap-6 lg:grid-cols-[280px_1fr]" aria-label="Tables">
        <ol className="space-y-1 lg:sticky lg:top-6 lg:self-start">
          {s.tables.map((t, i) => (
            <li key={t.name}>
              <button type="button" onClick={() => setTable(t.name)} aria-current={current?.name === t.name} className={cn("flex w-full items-baseline gap-3 rounded-xl px-3 py-2.5 text-left transition", current?.name === t.name ? "bg-navy text-ivory dark:bg-ivory dark:text-navy" : "hover:bg-surface-2")}>
                <span className="font-mono text-[0.62rem] opacity-70">{pad2(i + 1)}</span>
                <span className="font-serif text-lg font-semibold">{t.name}</span>
                <span className="ml-auto font-mono text-[0.6rem] opacity-70">{fmtInt(t.rows)}</span>
              </button>
            </li>
          ))}
        </ol>
        {current && (
          <div className="card overflow-hidden">
            <div className="border-b border-line px-6 py-5">
              <h3 className="display text-3xl">{current.name}</h3>
              <p className="label mt-1">{fmtInt(current.rows)} rows · {current.columns.length} columns</p>
            </div>
            <div className="scrollbar-thin overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="bg-surface-2 text-xs">
                  <tr>
                    <th scope="col" className="px-6 py-3 font-bold">Column</th>
                    <th scope="col" className="px-3 py-3 font-bold">Type</th>
                    <th scope="col" className="px-3 py-3 font-bold">Description</th>
                    <th scope="col" className="px-6 py-3 font-bold">Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {current.columns.map((c) => (
                    <tr key={c.name} className="border-t border-line align-top">
                      <td className="px-6 py-3">
                        <span className="flex items-center gap-2 font-semibold">
                          {c.pk && <KeyRound className="size-3.5 text-accent" aria-label="primary key" />}
                          {c.fk && <Link2 className="size-3.5 text-muted" aria-label="foreign key" />}
                          {c.name}
                          {c.pii && <span className="rounded bg-warn-soft px-1.5 font-mono text-[0.58rem] text-warn">PII · masked</span>}
                        </span>
                        {c.fk && <span className="mt-0.5 block font-mono text-[0.62rem] text-muted">→ {c.fk.table}.{c.fk.column}</span>}
                      </td>
                      <td className="px-3 py-3 font-mono text-xs uppercase text-muted">{c.type || "any"}</td>
                      <td className="max-w-md px-3 py-3 text-xs leading-relaxed text-ink-2">
                        {c.description || <span className="text-muted">—</span>}
                        {c.value_description && <span className="mt-1 block text-muted">{c.value_description}</span>}
                      </td>
                      <td className="px-6 py-3">
                        <span className="flex flex-wrap gap-1">
                          {c.samples.map((v) => (
                            <span key={v} className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.68rem]">{v}</span>
                          ))}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
