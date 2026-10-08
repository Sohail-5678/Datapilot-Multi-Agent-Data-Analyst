import type { Metadata } from "next";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";

export const metadata: Metadata = { title: "How it works" };

const NODES: { id: string; x: number; y: number; label: string; sub: string; tone?: "accent" | "human" | "guard" }[] = [
  { id: "guard", x: 60, y: 60, label: "Input guard", sub: "Prompt Guard 2 + rules", tone: "guard" },
  { id: "plan", x: 300, y: 60, label: "Planner", sub: "1–4 steps", tone: "accent" },
  { id: "clarify", x: 540, y: 60, label: "Clarify", sub: "interrupt()", tone: "human" },
  { id: "link", x: 300, y: 170, label: "Schema linker", sub: "BM25 · vectors · values" },
  { id: "c1", x: 120, y: 290, label: "Direct", sub: "Qwen 27B" },
  { id: "c2", x: 300, y: 290, label: "Plan → SQL", sub: "Qwen 27B" },
  { id: "c3", x: 480, y: 290, label: "Few-shot", sub: "Gemini Flash" },
  { id: "exec", x: 300, y: 400, label: "Guard → executor", sub: "read-only · repair ≤ 2", tone: "guard" },
  { id: "confirm", x: 560, y: 400, label: "Confirm", sub: "> 100k rows", tone: "human" },
  { id: "verify", x: 300, y: 510, label: "Vote + verifier", sub: "adaptive k", tone: "accent" },
  { id: "analyst", x: 60, y: 620, label: "Analyst", sub: "Pyodide in browser" },
  { id: "chart", x: 300, y: 620, label: "Chart", sub: "Vega-Lite subset" },
  { id: "narr", x: 540, y: 620, label: "Narrator → grounding", sub: "every number checked", tone: "guard" },
];
const EDGES: [string, string, boolean?][] = [
  ["guard", "plan"], ["plan", "clarify", true], ["plan", "link"], ["link", "c1"], ["link", "c2"], ["link", "c3"],
  ["c1", "exec"], ["c2", "exec"], ["c3", "exec"], ["exec", "confirm", true], ["exec", "verify"], ["verify", "analyst"], ["verify", "chart"], ["chart", "narr"],
];

function Graph() {
  const W = 180;
  const H = 58;
  const pos = Object.fromEntries(NODES.map((n) => [n.id, n]));
  return (
    <svg viewBox="0 0 760 700" className="w-full" role="img" aria-label="Agent graph: input guard, planner with clarify checkpoint, schema linker, three parallel SQL candidates, guard and read-only executor with confirm checkpoint, vote and verifier, then analyst, chart and narrator with the grounding guard.">
      {EDGES.map(([a, b, dashed]) => {
        const p = pos[a];
        const q = pos[b];
        const x1 = p.x + W / 2, y1 = p.y + H / 2, x2 = q.x + W / 2, y2 = q.y + H / 2;
        const my = (y1 + y2) / 2;
        return <path key={a + b} d={y1 === y2 ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1 + H / 2} C${x1},${my} ${x2},${my} ${x2},${y2 - H / 2}`} fill="none" stroke={dashed ? "var(--accent)" : "var(--line-strong)"} strokeWidth="1.2" strokeDasharray={dashed ? "5 5" : undefined} />;
      })}
      {NODES.map((n) => (
        <g key={n.id} transform={`translate(${n.x},${n.y})`}>
          <rect width={W} height={H} rx="12" fill={n.tone === "accent" ? "var(--navy)" : "var(--surface)"} stroke={n.tone === "human" ? "var(--accent)" : n.tone === "guard" ? "var(--positive)" : "var(--line-strong)"} strokeWidth={n.tone ? 1.5 : 1} strokeDasharray={n.tone === "human" ? "4 3" : undefined} />
          <text x={W / 2} y="26" textAnchor="middle" fontFamily="var(--font-cormorant), Georgia, serif" fontWeight="600" fontSize="17" fill={n.tone === "accent" ? "var(--ivory)" : "var(--ink)"}>{n.label}</text>
          <text x={W / 2} y="44" textAnchor="middle" fontFamily="var(--font-jetbrains), monospace" fontSize="9.5" letterSpacing="1" fill={n.tone === "accent" ? "var(--stone)" : "var(--muted)"}>{n.sub.toUpperCase()}</text>
        </g>
      ))}
    </svg>
  );
}

const GUARDS = [
  ["SQL guard", "sqlglot parses every statement: exactly one SELECT (or WITH … SELECT), no DDL/DML/PRAGMA/ATTACH, no dangerous functions, allow-listed tables, bounded recursion, no unconditioned cross joins on big tables, LIMIT ≤ 1,000."],
  ["Read-only engine", "SQLite opened with mode=ro&immutable=1, PRAGMA query_only=ON and an authorizer that only allows reads. The files are read-only in the container. Even a guard bug can't write."],
  ["Cost checkpoint", "EXPLAIN QUERY PLAN estimates scanned rows; above 100,000 the graph pauses and asks you — run, narrow down, or cancel."],
  ["Sandbox", "Model-written Python runs in Pyodide in a Web Worker in your browser. fetch, XHR, WebSocket and importScripts are replaced before user code; packages are frozen; 10-second kill switch."],
  ["Grounding", "Every number in the answer must appear in the results or be a derivation (difference, percent change, ratio, total) computed by code. Otherwise: one rewrite, then the sentence is removed — and you're told."],
  ["Budgets", "≤ 4 plan steps, ≤ 3 candidates, ≤ 2 repairs each, ≤ 25 LLM calls, ≤ 60K tokens and ≤ 90 s per question. Providers fall back on 429s; a circuit breaker opens after 5 failures."],
  ["Privacy", "Personal-data columns are masked in docs and never sent to a model as samples; traces are redacted before export. Guests get their own rate-limited identity."],
  ["Locked profile", "Prompts live in a versioned profile that AgentForge can optimize — but the guards, limits and PII policy are locked in code and rejected if a profile tries to set them."],
];

export default function About() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="eyebrow text-accent">How it works</p>
        <h1 className="display mt-3 max-w-4xl text-5xl sm:text-6xl">A supervised team, measured on a public benchmark.</h1>
        <p className="mt-6 max-w-3xl text-[0.98rem] leading-relaxed text-muted">
          DataPilot turns single-call text-to-SQL into a LangGraph team: a planner, a schema linker, three parallel SQL strategies, a
          guard and read-only executor with a repair loop, a voting verifier, a browser-side Python analyst, a chart agent and a
          narrator whose numbers are checked. Each addition is measured in the ablation on the Benchmarks page.
        </p>

        <section className="mt-14 grid gap-10 lg:grid-cols-[1.1fr_0.9fr]" aria-labelledby="graph">
          <div className="card p-6 sm:p-8">
            <h2 id="graph" className="label mb-4">The agent graph</h2>
            <Graph />
            <p className="mt-3 text-xs text-muted">Dashed crimson = human checkpoints (LangGraph interrupt → resume). Green = guards.</p>
          </div>
          <div className="space-y-6">
            <div className="card-flat p-6">
              <h3 className="font-serif text-2xl font-semibold">Stack at $0</h3>
              <ul className="mt-4 space-y-2.5 text-sm text-ink-2">
                <li><b>Web</b> — Next.js on Vercel Hobby: Auth.js, a JWT-minting proxy, the Pyodide worker, Vega-Lite.</li>
                <li><b>API</b> — FastAPI + LangGraph in one Docker container on Render free (512 MB), read-only SQLite demo databases baked in.</li>
                <li><b>Models</b> — Gemini Flash / Flash-Lite and Groq Qwen 27B free tiers, Prompt Guard 2; model ids are env vars.</li>
                <li><b>Data</b> — Neon Postgres for threads, traces, caches and the quota ledger.</li>
                <li><b>Evals</b> — BIRD Mini-Dev in GitHub Actions; traces in the shared trace.v1 format for AgentForge.</li>
              </ul>
            </div>
            <div className="card-flat p-6">
              <h3 className="font-serif text-2xl font-semibold">Why it&apos;s measured</h3>
              <p className="mt-3 text-sm leading-relaxed text-ink-2">
                Linking, candidates, repair and verification each cost calls. The ablation (C0 single call → C4 full + adaptive) shows what each
                one buys in execution accuracy and what it costs at list price, so the trade-off is a chart, not a claim.
              </p>
            </div>
          </div>
        </section>

        <section className="mt-20" aria-labelledby="guardrails">
          <h2 id="guardrails" className="display text-4xl">Guardrails, in order of defense.</h2>
          <ol className="mt-10 grid gap-x-10 sm:grid-cols-2">
            {GUARDS.map(([t, d], i) => (
              <li key={t} className="border-t border-line py-6">
                <div className="flex items-baseline gap-4">
                  <span className="font-mono text-[0.7rem] tracking-[0.2em] text-accent">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <h3 className="font-serif text-2xl font-semibold">{t}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted">{d}</p>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="card mt-16 p-8" aria-labelledby="agentforge">
          <h2 id="agentforge" className="display text-3xl">Built to be evaluated.</h2>
          <p className="mt-3 max-w-3xl text-sm leading-relaxed text-muted">
            Every run emits a <code className="font-mono">trace.v1</code> record and loads a versioned <code className="font-mono">profile.v1</code>.
            An eval adapter CLI runs <code className="font-mono">case.v1</code> files against fresh database copies, with red-team seed overrides
            (injection strings in rows, a canary table) — so a separate platform, AgentForge, can score, attack and optimize DataPilot without
            touching its guards.
          </p>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
