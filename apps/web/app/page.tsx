import { ArrowRight, Braces, Cpu, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { getAppUser } from "@/auth";
import { guestSignInAction } from "@/app/actions";
import { Astrolabe } from "@/components/brand/astrolabe";
import { Star } from "@/components/brand/logo";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";
import { benchSummary } from "@/lib/bench";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  {
    n: "01",
    db: "chinook",
    tag: "CHINOOK · RANKING",
    q: "Which 5 genres made the most revenue in 2012, and how did each change vs 2011?",
    tone: "bg-navy text-ivory",
    rule: "bg-ivory/40",
    tagTone: "text-stone",
  },
  {
    n: "02",
    db: "chinook",
    tag: "CHINOOK · PYTHON SANDBOX",
    q: "Is there a correlation between track length and price?",
    tone: "bg-stone text-navy",
    rule: "bg-navy/40",
    tagTone: "text-navy/70",
  },
  {
    n: "03",
    db: "european_football_2",
    tag: "FOOTBALL · HUMAN CHECKPOINT",
    q: "Show me the best players",
    tone: "bg-crimson text-white",
    rule: "bg-white/50",
    tagTone: "text-white/80",
  },
];

const CREW = [
  ["Planner", "Breaks the question into steps — or asks you what you meant."],
  ["Schema linker", "Finds the right tables, columns, join paths and the exact spelling of values."],
  ["SQL agents ×3", "Three strategies write candidates in parallel: direct, plan-then-SQL, few-shot."],
  ["SQL guard", "Parses every statement; anything but one bounded SELECT is rejected."],
  ["Executor", "Read-only connection, read-only files, a 5-second timeout and a row cap."],
  ["Verifier", "Candidates vote by result; a checker confirms columns, filters and grain."],
  ["Analyst", "Writes pandas that runs in your browser — Python never runs on the server."],
  ["Chart agent", "Emits a validated Vega-Lite spec; the data is injected by your browser."],
  ["Narrator", "Writes the answer in under 120 words, from the results only."],
  ["Output guard", "Checks every number against the data and removes anything unsupported."],
];

function ExampleCard({ e, signedIn, i }: { e: (typeof EXAMPLES)[number]; signedIn: boolean; i: number }) {
  const href = `/app?db=${e.db}&q=${encodeURIComponent(e.q)}`;
  const body = (
    <>
      <span className="font-serif text-xl italic opacity-80">{e.n}</span>
      <span className="mt-auto block">
        <span className="display block text-[1.65rem] leading-[1.12] sm:text-[1.8rem]">{e.q}</span>
        <span className={cn("mt-5 block h-px w-24", e.rule)} />
        <span className={cn("mt-4 flex items-center justify-between font-mono text-[0.62rem] tracking-[0.24em]", e.tagTone)}>
          {e.tag}
          <ArrowRight className="size-4 transition-transform duration-500 ease-[var(--ease-velora)] group-hover:translate-x-1.5" />
        </span>
      </span>
    </>
  );
  const cls = cn(
    "swatch group flex min-h-[19rem] w-full flex-col p-7 text-left transition-transform duration-700 ease-[var(--ease-velora)] hover:-translate-y-2",
    e.tone,
    i === 1 ? "lg:translate-y-10 lg:hover:translate-y-8" : "",
  );
  if (signedIn) {
    return (
      <Link href={href} className={cls}>
        {body}
      </Link>
    );
  }
  return (
    <form action={guestSignInAction} className="contents">
      <input type="hidden" name="next" value={href} />
      <button type="submit" className={cls} aria-label={`Try: ${e.q}`}>
        {body}
      </button>
    </form>
  );
}

export default async function Landing() {
  const user = await getAppUser();
  const bench = benchSummary();
  const c0 = bench.configs.find((c) => c.config === "C0");
  const best = [...bench.configs].sort((a, b) => b.ex - a.ex)[0];
  return (
    <div className="relative">
      <div className="relative overflow-hidden bg-navy text-ivory">
        <div className="pointer-events-none absolute inset-0 opacity-60 [background:radial-gradient(60%_60%_at_75%_35%,rgba(175,174,162,0.12),transparent_70%)]" />
        <SiteHeader variant="navy" />
        <main id="main" className="relative mx-auto grid max-w-[1440px] items-center gap-10 px-4 pb-20 pt-10 sm:px-8 lg:grid-cols-[1.05fr_1fr] lg:pb-28 lg:pt-16">
          <div>
            <p className="eyebrow text-stone">Vol. 01 — Multi-agent data analyst</p>
            <h1 className="display mt-6 text-[3.1rem] sm:text-[4.4rem] xl:text-[5.4rem]">
              Ask in plain English.
              <br />
              <em className="font-normal text-crimson-bright">Get answers you can verify.</em>
            </h1>
            <p className="mt-7 max-w-xl text-[1.05rem] leading-relaxed text-ivory/80">
              DataPilot is a team of agents that plans your question, writes three SQL candidates, lets them vote, runs the winner
              read-only, and checks every number in the answer against the data. Open any answer and see exactly what each agent did.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              {user ? (
                <Link href="/app" className="btn btn-crimson">
                  Open the workspace <ArrowRight className="size-4" />
                </Link>
              ) : (
                <form action={guestSignInAction}>
                  <input type="hidden" name="next" value="/app" />
                  <button type="submit" className="btn btn-crimson">
                    Try it — no sign-up <ArrowRight className="size-4" />
                  </button>
                </form>
              )}
              <Link href="/about" className="btn border border-ivory/30 text-ivory hover:border-ivory/70 hover:bg-ivory/5">
                How it works
              </Link>
            </div>
            <dl className="mt-12 grid max-w-xl grid-cols-3 gap-6 border-t border-ivory/15 pt-6">
              {[
                ["3", "SQL strategies vote"],
                ["0", "writes possible"],
                ["100%", "numbers checked"],
              ].map(([k, v]) => (
                <div key={v}>
                  <dt className="sr-only">{v}</dt>
                  <dd className="display text-4xl">{k}</dd>
                  <dd className="mt-1 font-mono text-[0.6rem] uppercase tracking-[0.22em] text-stone">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="relative mx-auto w-full max-w-[560px]">
            <Astrolabe className="w-full drop-shadow-[0_40px_80px_rgba(0,0,0,0.45)]" />
            <p className="absolute bottom-2 right-0 font-mono text-[0.58rem] tracking-[0.24em] text-stone/80">FIG. 1 — THE PILOT&apos;S INSTRUMENT</p>
          </div>
        </main>
      </div>

      <section className="relative z-10 mx-auto max-w-[1440px] px-4 pt-20 sm:px-8" aria-labelledby="try">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <p className="eyebrow text-accent">Try one</p>
            <h2 id="try" className="display mt-3 text-4xl sm:text-5xl">
              Three questions, three kinds of thinking.
            </h2>
          </div>
          <p className="max-w-md text-sm leading-relaxed text-muted">
            A ranking with year-over-year change, a statistic computed by Python in your browser, and a vague question the planner
            refuses to guess at.
          </p>
        </div>
        <div className="mt-12 grid gap-10 sm:grid-cols-2 lg:grid-cols-3 lg:gap-12">
          {EXAMPLES.map((e, i) => (
            <ExampleCard key={e.n} e={e} i={i} signedIn={Boolean(user)} />
          ))}
        </div>
      </section>

      <section className="relative z-10 mx-auto mt-32 max-w-[1440px] px-4 sm:px-8" aria-labelledby="crew">
        <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr]">
          <div className="lg:sticky lg:top-10 lg:self-start">
            <p className="eyebrow text-accent">The crew</p>
            <h2 id="crew" className="display mt-3 text-4xl sm:text-5xl">
              Ten specialists.
              <br />
              <em className="text-ink-2">One verified answer.</em>
            </h2>
            <p className="mt-6 max-w-md text-[0.95rem] leading-relaxed text-muted">
              Not one prompt that guesses SQL. A supervised LangGraph team with parallel candidates, self-repair, human checkpoints,
              budgets and stop rules — every step traced.
            </p>
            <Link href="/about" className="btn btn-ghost mt-8">
              See the architecture <ArrowRight className="size-4" />
            </Link>
          </div>
          <ol className="grid gap-x-10 sm:grid-cols-2">
            {CREW.map(([name, desc], i) => (
              <li key={name} className="group border-t border-line py-6">
                <div className="flex items-baseline gap-4">
                  <span className="font-mono text-[0.7rem] tracking-[0.2em] text-accent">{String(i + 1).padStart(2, "0")}</span>
                  <div>
                    <h3 className="font-serif text-2xl font-semibold">{name}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted">{desc}</p>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="relative z-10 mx-auto mt-32 max-w-[1440px] px-4 sm:px-8" aria-labelledby="guarantees">
        <h2 id="guarantees" className="sr-only">
          Guarantees
        </h2>
        <div className="grid gap-6 md:grid-cols-3">
          {[
            { icon: ShieldCheck, t: "Read-only, three ways", d: "A SQL parser, a read-only engine connection and read-only files. Even a guard bug can't write." },
            { icon: Cpu, t: "Python runs in your browser", d: "Analysis code runs in Pyodide (WebAssembly) in a Web Worker with network access removed." },
            { icon: Braces, t: "Every number is checked", d: "The answer's numbers must come from the results — or a derivation code can recompute. Else they're cut." },
          ].map(({ icon: Icon, t, d }, i) => (
            <div key={t} className="card relative overflow-hidden p-8">
              <span className="absolute right-6 top-5 font-serif text-5xl italic text-line-strong">{String(i + 1).padStart(2, "0")}</span>
              <Icon className="size-6 text-accent" />
              <h3 className="mt-6 font-serif text-2xl font-semibold">{t}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="relative z-10 mx-auto mt-24 max-w-[1440px] px-4 sm:px-8" aria-labelledby="bench">
        <div className="card grid items-center gap-8 overflow-hidden p-8 sm:p-12 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <p className="eyebrow text-accent">Measured, not claimed</p>
            <h2 id="bench" className="display mt-3 text-4xl">Scored on BIRD Mini-Dev.</h2>
            <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted">
              A fixed, stratified 150-question subset of the public BIRD Mini-Dev benchmark, an ablation that turns each agent on one at
              a time, and a cost-vs-accuracy chart — with 95% bootstrap intervals and the failures left in.
            </p>
            <Link href="/benchmarks" className="btn btn-primary mt-7">
              Open the benchmarks <ArrowRight className="size-4" />
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-4">
            {bench.available && c0 && best ? (
              <>
                <Stat k={`${(c0.ex * 100).toFixed(1)}%`} v={`Single-call baseline · n=${c0.n}`} />
                <Stat k={`${(best.ex * 100).toFixed(1)}%`} v={`${best.config} best config · n=${best.n}`} accent />
              </>
            ) : (
              <div className="col-span-2 rounded-2xl border border-dashed border-line-strong p-6">
                <p className="flex items-center gap-2 font-serif text-xl">
                  <Sparkles className="size-4 text-accent" /> Benchmark run in progress
                </p>
                <p className="mt-2 text-sm text-muted">
                  Free-tier rate limits mean the 150 questions run over several nights. Results appear here as soon as they are real.
                </p>
              </div>
            )}
          </div>
        </div>
      </section>

      <div className="relative z-10 mx-auto mt-24 flex max-w-[1440px] items-center justify-center gap-3 px-4 text-muted">
        <Star className="size-3 text-accent" />
        <span className="font-mono text-[0.62rem] tracking-[0.28em]">PLAN · LINK · VOTE · VERIFY · EXPLAIN</span>
        <Star className="size-3 text-accent" />
      </div>
      <SiteFooter />
    </div>
  );
}

function Stat({ k, v, accent }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className={cn("rounded-2xl p-6", accent ? "bg-navy text-ivory" : "bg-surface-2")}>
      <p className="display text-5xl">{k}</p>
      <p className={cn("mt-2 font-mono text-[0.6rem] uppercase tracking-[0.2em]", accent ? "text-stone" : "text-muted")}>{v}</p>
    </div>
  );
}
