import type { Metadata } from "next";
import { BenchCharts, FailedCases } from "@/components/bench/charts";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";
import { benchSummary } from "@/lib/bench";
import { fmtMs, fmtUsd } from "@/lib/utils";

export const metadata: Metadata = { title: "Benchmarks" };

const CONFIG_INFO: Record<string, [string, string]> = {
  C0: ["Baseline", "One call, full schema, evidence included"],
  C1: ["+ linking", "Schema + value linking, pruned schema"],
  C2: ["+ candidates", "k = 3 strategies + result voting"],
  C3: ["+ repair + verifier", "Repair loop (≤ 2) + LLM verifier"],
  C4: ["Full + adaptive", "Adaptive k, verified cache, few-shot from train"],
};

const pct = (v: number | undefined) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

export default function Benchmarks() {
  const b = benchSummary();
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="eyebrow text-accent">BIRD Mini-Dev · execution accuracy</p>
        <h1 className="display mt-3 max-w-4xl text-5xl sm:text-6xl">What each agent buys — and what it costs.</h1>
        <p className="mt-6 max-w-3xl text-[0.98rem] leading-relaxed text-muted">
          A fixed, stratified 150-question subset of BIRD Mini-Dev (SQLite, with evidence; seed 13; split 50 train / 50 val / 50 test).
          Execution accuracy (EX) uses BIRD&apos;s official set comparison against the gold SQL on the same database file. Costs are tokens ×
          published paid list prices — the project itself runs at $0 on free tiers.
        </p>

        {!b.available || !b.configs.length ? (
          <section className="card mt-12 p-8 sm:p-12">
            <h2 className="display text-3xl">The first full run is still accumulating.</h2>
            <p className="mt-4 max-w-2xl text-sm leading-relaxed text-muted">
              Free model tiers allow roughly 1,000 requests and 8,000 tokens a minute per model, so the five configurations over 150
              questions run in resumable batches across several nights in GitHub Actions. This page only ever shows numbers that came out of
              the harness — nothing here is estimated.
            </p>
            <pre className="mt-6 overflow-x-auto rounded-2xl bg-[var(--code-bg)] p-5 font-mono text-xs leading-relaxed text-[var(--code-ink)]">
{`cd backend
uv run python -m datapilot.bench --config C0 --split test --resume
uv run python -m datapilot.bench --config C4 --split test --resume`}
            </pre>
          </section>
        ) : (
          <>
            {b.note && <p className="mt-8 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn">{b.note}</p>}
            <section className="card mt-12 overflow-hidden" aria-labelledby="ablation">
              <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-6 py-5">
                <h2 id="ablation" className="display text-3xl">Ablation</h2>
                {b.updated_at && <p className="label">Updated {b.updated_at.slice(0, 10)}</p>}
              </div>
              <div className="scrollbar-thin overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-sm">
                  <thead className="bg-surface-2 text-xs">
                    <tr>
                      {["Config", "What's on", "n", "EX", "95% CI", "LLM calls / q", "Tokens / q", "List price / q", "p50 / p95"].map((h) => (
                        <th key={h} scope="col" className="px-5 py-3 font-bold">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.configs.map((c) => (
                      <tr key={c.config} className="border-t border-line">
                        <td className="px-5 py-4">
                          <span className="font-mono text-xs text-accent">{c.config}</span>
                          <span className="ml-2 font-serif text-lg font-semibold">{CONFIG_INFO[c.config]?.[0] ?? c.label}</span>
                          {c.preview && <span className="ml-2 chip !py-0 !text-[0.6rem] text-warn">preview</span>}
                        </td>
                        <td className="px-5 py-4 text-xs text-muted">{CONFIG_INFO[c.config]?.[1]}</td>
                        <td className="px-5 py-4 font-mono">{c.n}</td>
                        <td className="px-5 py-4 font-serif text-2xl font-semibold">{pct(c.ex)}</td>
                        <td className="px-5 py-4 font-mono text-xs text-muted">{c.ci_low != null ? `${pct(c.ci_low)} – ${pct(c.ci_high)}` : "—"}</td>
                        <td className="px-5 py-4 font-mono">{c.llm_calls_per_q?.toFixed(1) ?? "—"}</td>
                        <td className="px-5 py-4 font-mono">{c.tokens_per_q ? Math.round(c.tokens_per_q).toLocaleString() : "—"}</td>
                        <td className="px-5 py-4 font-mono">{fmtUsd(c.cost_per_q)}</td>
                        <td className="px-5 py-4 font-mono text-xs">{fmtMs(c.p50_ms)} / {fmtMs(c.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <BenchCharts configs={b.configs} />
            <FailedCases cases={b.failed_cases ?? []} />
          </>
        )}
      </main>
      <SiteFooter />
    </>
  );
}
