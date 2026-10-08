"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { UploadDialog } from "@/components/data/upload-dialog";
import { api } from "@/lib/api";
import type { DatabaseInfo, DatabasesResponse } from "@/lib/types";
import { fmtInt, pad2 } from "@/lib/utils";

/** "Your data": the signed-in visitor's private uploaded datasets, with upload and delete. */
export function YourData({ signedIn }: { signedIn: boolean }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["dbs"], queryFn: () => api<DatabasesResponse>("databases"), enabled: signedIn });
  const mine = q.data?.uploaded ?? [];
  const del = async (d: DatabaseInfo) => {
    if (!window.confirm(`Delete “${d.title}”? This can't be undone.`)) return;
    await api(`datasets/${d.db_id}`, { method: "DELETE" }).catch(() => {});
    await qc.invalidateQueries({ queryKey: ["dbs"] });
  };
  return (
    <section className="mt-14" aria-labelledby="your-data">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow text-accent">Your data</p>
          <h2 id="your-data" className="display mt-2 text-3xl">Bring your own spreadsheet.</h2>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Upload CSV, Excel, JSON or SQLite files and ask about them with the same agents, guards and checks. Private to you; deleted
            automatically.
          </p>
        </div>
        {signedIn ? (
          <button type="button" className="btn btn-crimson" onClick={() => setOpen(true)}>
            <Upload className="size-4" /> Upload data
          </button>
        ) : (
          <Link href="/login?next=/databases" className="btn btn-primary">Sign in to upload <ArrowRight className="size-4" /></Link>
        )}
      </div>
      {mine.length > 0 && (
        <ol className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {mine.map((d, i) => (
            <li key={d.db_id} className="card flex flex-col p-5">
              <div className="flex items-baseline gap-3">
                <span className="font-serif text-lg italic text-accent">{pad2(i + 1)}</span>
                <Link href={`/databases/${d.db_id}`} className="display truncate text-2xl hover:underline">{d.title}</Link>
              </div>
              <p className="mt-2 line-clamp-2 text-xs text-muted">{d.description}</p>
              <p className="mt-3 font-mono text-[0.6rem] uppercase tracking-[0.16em] text-muted">
                {d.tables} tables · {fmtInt(d.rows)} rows{d.expires_at ? ` · until ${new Date(d.expires_at).toLocaleDateString()}` : ""}
              </p>
              <div className="mt-4 flex gap-2">
                <Link href={`/app?db=${d.db_id}`} className="btn btn-primary !px-3 !py-1.5 text-xs">Ask <ArrowRight className="size-3.5" /></Link>
                <button type="button" onClick={() => del(d)} className="btn !px-3 !py-1.5 text-xs text-muted hover:bg-accent-soft hover:text-accent-ink" aria-label={`Delete ${d.title}`}>
                  <Trash2 className="size-3.5" /> Delete
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}
      <UploadDialog
        open={open}
        onClose={() => setOpen(false)}
        onDone={async (ds) => {
          setOpen(false);
          await qc.invalidateQueries({ queryKey: ["dbs"] });
          router.push(`/app?db=${ds.db_id}`);
        }}
      />
    </section>
  );
}
