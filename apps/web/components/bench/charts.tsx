"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ErrorBar, LabelList, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { CodeBlock } from "@/components/ui/code";
import type { BenchConfigRow, BenchSummary } from "@/lib/bench";
import { cn } from "@/lib/utils";

const INK = "var(--ink)";
const MUTED = "var(--muted)";
const GRID = "var(--line)";
const tick = { fill: MUTED, fontSize: 11, fontFamily: "var(--font-jetbrains)" };

export function BenchCharts({ configs }: { configs: BenchConfigRow[] }) {
  const scatter = configs.filter((c) => c.cost_per_q != null).map((c) => ({ x: (c.cost_per_q ?? 0) * 1000, y: c.ex * 100, z: c.n, name: c.config }));
  const difficulties = ["simple", "moderate", "challenging"];
  const diff = difficulties.map((d) => ({ d, ...Object.fromEntries(configs.map((c) => [c.config, c.per_difficulty?.[d] ? c.per_difficulty[d].ex * 100 : null])) }));
  const dbs = Array.from(new Set(configs.flatMap((c) => Object.keys(c.per_db ?? {}))));
  const best = [...configs].sort((a, b) => b.ex - a.ex)[0];
  const perDb = dbs.map((db) => ({ db, ex: best?.per_db?.[db] ? best.per_db[db].ex * 100 : 0, n: best?.per_db?.[db]?.n ?? 0 }));
  const COLORS = ["#8f8e83", "#4b4f8f", "#1b1e4a", "#e58a8d", "#d21319"];
  const exBars = configs.map((c) => ({ config: c.config, ex: c.ex * 100, err: c.ci_low != null && c.ci_high != null ? [c.ex * 100 - c.ci_low * 100, c.ci_high * 100 - c.ex * 100] : [0, 0] }));
  return (
    <div className="mt-10 grid gap-8 lg:grid-cols-2">
      <figure className="card p-6">
        <figcaption className="label mb-4">Execution accuracy with 95% bootstrap CI</figcaption>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={exBars} margin={{ top: 20, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="config" tick={tick} axisLine={false} tickLine={false} />
            <YAxis tick={tick} axisLine={false} tickLine={false} unit="%" domain={[0, 100]} />
            <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12 }} />
            <Bar dataKey="ex" fill="#1b1e4a" radius={[6, 6, 0, 0]} maxBarSize={72} className="[&_path]:fill-[var(--ink)]">
              <ErrorBar dataKey="err" width={8} stroke="var(--accent)" />
              <LabelList dataKey="ex" position="top" formatter={(v) => `${Number(v).toFixed(1)}`} style={{ fill: INK, fontSize: 11, fontFamily: "var(--font-jetbrains)" }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </figure>
      <figure className="card p-6">
        <figcaption className="label mb-4">Accuracy vs list-price cost per 1,000 questions</figcaption>
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart margin={{ top: 20, right: 20, bottom: 10, left: -10 }}>
            <CartesianGrid stroke={GRID} />
            <XAxis type="number" dataKey="x" name="cost" unit=" $" tick={tick} axisLine={false} tickLine={false} />
            <YAxis type="number" dataKey="y" name="EX" unit="%" tick={tick} axisLine={false} tickLine={false} domain={[0, 100]} />
            <ZAxis dataKey="z" range={[90, 90]} />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} formatter={(v, n) => (n === "EX" ? `${Number(v).toFixed(1)}%` : `$${Number(v).toFixed(2)}`)} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12 }} />
            <Scatter data={scatter} fill="var(--accent)">
              <LabelList dataKey="name" position="right" style={{ fill: INK, fontSize: 11, fontFamily: "var(--font-jetbrains)" }} />
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </figure>
      <figure className="card p-6">
        <figcaption className="label mb-4">EX by difficulty</figcaption>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={diff} margin={{ top: 10, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="d" tick={tick} axisLine={false} tickLine={false} />
            <YAxis tick={tick} axisLine={false} tickLine={false} unit="%" domain={[0, 100]} />
            <Tooltip formatter={(v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`)} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12 }} />
            {configs.map((c, i) => (
              <Bar key={c.config} dataKey={c.config} fill={COLORS[i % COLORS.length]} radius={[4, 4, 0, 0]} maxBarSize={56} />
            ))}
          </BarChart>
        </ResponsiveContainer>
        <p className="mt-2 flex flex-wrap gap-3 text-xs text-muted">
          {configs.map((c, i) => (
            <span key={c.config} className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: COLORS[i % COLORS.length] }} />{c.config}</span>
          ))}
        </p>
      </figure>
      <figure className="card p-6">
        <figcaption className="label mb-4">EX by database · {best?.config}</figcaption>
        <ResponsiveContainer width="100%" height={Math.max(220, perDb.length * 30)}>
          <BarChart data={perDb} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 40 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tick={tick} axisLine={false} tickLine={false} unit="%" domain={[0, 100]} />
            <YAxis type="category" dataKey="db" tick={{ ...tick, fontSize: 10 }} width={130} axisLine={false} tickLine={false} />
            <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 12 }} />
            <Bar dataKey="ex" fill="var(--accent)" radius={[0, 4, 4, 0]}>
              <LabelList dataKey="n" position="right" formatter={(v) => `n=${v}`} style={{ fill: MUTED, fontSize: 10, fontFamily: "var(--font-jetbrains)" }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </figure>
    </div>
  );
}

export function FailedCases({ cases }: { cases: NonNullable<BenchSummary["failed_cases"]> }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!cases.length) return null;
  return (
    <section className="mt-12" aria-labelledby="failed">
      <h2 id="failed" className="display text-3xl">Failures, left in.</h2>
      <p className="mt-2 text-sm text-muted">Questions the best configuration got wrong, with the gold and predicted SQL side by side.</p>
      <ol className="mt-6 divide-y divide-line rounded-2xl border border-line">
        {cases.map((c, i) => (
          <li key={`${c.config}-${c.question_id}`}>
            <button type="button" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i} className="flex w-full items-start gap-4 px-5 py-4 text-left hover:bg-surface-2">
              <span className="font-mono text-[0.62rem] text-accent">{c.config}</span>
              <span className="flex-1 text-sm">{c.question}</span>
              <span className={cn("chip !py-0 !text-[0.6rem]", c.difficulty === "challenging" ? "text-accent-ink" : "text-muted")}>{c.difficulty}</span>
              <span className="hidden font-mono text-[0.62rem] text-muted sm:inline">{c.db_id}</span>
            </button>
            {open === i && (
              <div className="grid gap-4 px-5 pb-5 lg:grid-cols-2">
                <CodeBlock code={c.gold_sql} lang="sql" label="gold SQL" />
                <CodeBlock code={c.pred_sql ?? `-- no SQL produced${c.error ? `: ${c.error}` : ""}`} lang="sql" label="predicted SQL" />
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
