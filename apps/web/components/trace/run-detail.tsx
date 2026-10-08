"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ConfidenceBadge } from "@/components/answer/answer-card";
import { RichText } from "@/components/answer/rich-text";
import { Waking } from "@/components/db/catalog";
import { TraceTimeline } from "@/components/trace/trace-timeline";
import { CodeBlock } from "@/components/ui/code";
import { api, ApiError } from "@/lib/api";
import { STRATEGY_LABEL } from "@/lib/turn";
import type { RunRecord } from "@/lib/types";
import { fmtInt, fmtMs, fmtUsd } from "@/lib/utils";

export function RunDetail({ runId }: { runId: string }) {
  const q = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api<RunRecord>(`runs/${runId}`),
    retry: (n, e) => (e instanceof ApiError && e.code === "backend_starting" ? n < 20 : false),
    retryDelay: 3000,
  });
  if (q.isPending) return <div className="dp-shimmer h-96 rounded-2xl" />;
  if (q.isError) {
    if (q.error instanceof ApiError && q.error.status === 404)
      return (
        <div className="py-20 text-center">
          <h1 className="display text-5xl">Run not found.</h1>
          <p className="mt-3 text-muted">Runs are private to the person who asked, and kept for 30 days.</p>
          <Link href="/app" className="btn btn-primary mt-8">Back to the workspace</Link>
        </div>
      );
    return <Waking error={q.error} />;
  }
  const r = q.data;
  const m = r.detail?.metrics;
  const steps = r.detail?.steps ?? [];
  return (
    <div className="space-y-10">
      <div>
        <Link href={`/app/${r.thread_id}`} className="label hover:text-ink">← Back to the conversation</Link>
        <p className="eyebrow mt-8 text-accent">Trace · {r.db_id}</p>
        <h1 className="display mt-3 text-4xl sm:text-5xl">{r.question}</h1>
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <span className="chip">{r.status}</span>
          <ConfidenceBadge confidence={r.confidence} reasons={steps[steps.length - 1]?.reasons} />
          <span className="chip text-muted">{r.profile_version}</span>
        </div>
      </div>
      {m && (
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          {[
            ["LLM calls", String(m.llm_calls)],
            ["Tokens", fmtInt(m.tokens_in + m.tokens_out)],
            ["Latency", fmtMs(m.latency_ms)],
            ["List price", fmtUsd(m.list_price_cost_usd)],
            ["Spans", String(r.spans?.length ?? 0)],
          ].map(([k, v]) => (
            <div key={k} className="card-flat p-4">
              <dt className="label">{k}</dt>
              <dd className="display mt-1 text-3xl">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {r.answer && (
        <section className="card p-6">
          <p className="label mb-3">Answer</p>
          <RichText text={r.answer} className="prose-answer font-serif text-xl leading-relaxed" />
        </section>
      )}
      {steps.map((s) => (
        <section key={s.step_idx} className="space-y-3">
          <p className="label">Step {s.step_idx + 1} · {s.goal}</p>
          {s.sql && <CodeBlock code={s.sql} lang="sql" label={`chosen SQL · ${s.confidence}`} />}
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-2 pr-3 font-semibold">Candidate</th>
                  <th className="py-2 pr-3 font-semibold">Model</th>
                  <th className="py-2 pr-3 font-semibold">Status</th>
                  <th className="py-2 pr-3 font-semibold">Rows</th>
                  <th className="py-2 pr-3 font-semibold">Repairs</th>
                  <th className="py-2 font-semibold">Result hash</th>
                </tr>
              </thead>
              <tbody>
                {s.candidates.map((c) => (
                  <tr key={c.id} className="border-t border-line">
                    <td className="py-2 pr-3 font-semibold">{STRATEGY_LABEL[c.strategy] ?? c.strategy}</td>
                    <td className="py-2 pr-3 font-mono text-muted">{c.model ?? "—"}</td>
                    <td className="py-2 pr-3">{c.status}</td>
                    <td className="py-2 pr-3 font-mono">{c.row_count ?? "—"}</td>
                    <td className="py-2 pr-3 font-mono">{c.repairs ?? 0}</td>
                    <td className="py-2 font-mono text-muted">{c.result_hash ?? c.error?.slice(0, 60) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <section>
        <h2 className="display mb-4 text-3xl">Timeline</h2>
        <TraceTimeline spans={r.spans ?? []} />
      </section>
    </div>
  );
}
