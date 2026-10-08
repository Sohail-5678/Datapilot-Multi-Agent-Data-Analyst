"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Loader2 } from "lucide-react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { DatabaseInfo } from "@/lib/types";
import { cn, fmtInt, pad2 } from "@/lib/utils";

const TONES = [
  "bg-navy text-ivory",
  "bg-stone text-navy",
  "bg-crimson text-white",
  "bg-surface text-ink",
  "bg-navy-ink text-ivory",
];

export function useDatabases() {
  return useQuery({
    queryKey: ["dbs"],
    queryFn: () => api<{ databases: DatabaseInfo[] }>("databases").then((r) => r.databases),
    retry: (n, e) => (e instanceof ApiError && e.code === "backend_starting" ? n < 20 : n < 1),
    retryDelay: 3000,
  });
}

export function Waking({ error }: { error: unknown }) {
  const starting = error instanceof ApiError && (error.code === "backend_starting" || error.status === 503);
  return (
    <div className="card mt-10 flex items-center gap-3 p-6 text-sm" role="status">
      {starting ? <Loader2 className="size-4 animate-spin text-accent" /> : null}
      {starting ? "Waking up the free server — this takes up to a minute after it has been idle." : error instanceof Error ? error.message : "Couldn't load."}
    </div>
  );
}

export function DatabaseCatalog() {
  const q = useDatabases();
  if (q.isPending || (q.isError && q.failureCount < 20 && q.isFetching)) {
    return (
      <div className="mt-14 grid gap-10 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="dp-shimmer h-72 rounded-md" />
        ))}
      </div>
    );
  }
  if (q.isError) return <Waking error={q.error} />;
  return (
    <ol className="mt-14 grid gap-10 sm:grid-cols-2 lg:grid-cols-3">
      {q.data.map((d, i) => (
        <li key={d.db_id}>
          <Link href={`/databases/${d.db_id}`} className={cn("swatch group flex min-h-[19rem] flex-col p-7 transition-transform duration-700 ease-[var(--ease-velora)] hover:-translate-y-2", TONES[i % TONES.length])}>
            <span className="font-serif text-xl italic opacity-80">{pad2(i + 1)}</span>
            <span className="mt-auto">
              <span className="block font-mono text-[0.6rem] uppercase tracking-[0.24em] opacity-75">{d.subtitle}</span>
              <span className="display mt-2 block text-4xl">{d.title}</span>
              <span className="mt-3 block text-sm leading-relaxed opacity-80">{d.description}</span>
              <span className="mt-5 block h-px w-24 bg-current opacity-40" />
              <span className="mt-4 flex items-center justify-between font-mono text-[0.6rem] tracking-[0.22em] opacity-80">
                #{d.tables} TABLES · {fmtInt(d.rows)} ROWS
                <ArrowRight className="size-4 transition-transform group-hover:translate-x-1.5" />
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ol>
  );
}
