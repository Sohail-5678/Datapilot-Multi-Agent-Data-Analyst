"use client";

import { AlertOctagon, Check, ChevronDown, CircleDashed, Loader2, ScanSearch, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AnswerCard } from "@/components/answer/answer-card";
import { CodeBlock } from "@/components/ui/code";
import { Dots } from "@/components/ui/dots";
import { STRATEGY_LABEL, type Turn } from "@/lib/turn";
import { cn, fmtInt, fmtMs } from "@/lib/utils";

const ERROR_COPY: Record<string, string> = {
  quota_exhausted: "Free AI quota for today is used up; you can still browse benchmarks and past answers.",
  rate_limited: "You're asking quickly — the free tier allows a few questions per hour. Try again shortly.",
  backend_starting: "Waking up the free server — this takes up to a minute after it has been idle. Try again in a moment.",
  backend_unconfigured: "The API server isn't connected yet.",
};

function PlanSteps({ turn }: { turn: Turn }) {
  if (!turn.plan.length) return null;
  const active = turn.phase !== "done" && turn.phase !== "error";
  return (
    <div className="card-flat px-5 py-4">
      <p className="label mb-3">Plan</p>
      <ol className="space-y-2.5">
        {turn.plan.map((s, i) => {
          const done = i < turn.stepsDone || turn.phase === "done";
          const current = active && i === turn.stepIdx;
          return (
            <li key={i} className="flex items-start gap-3">
              <span
                className={cn(
                  "mt-0.5 grid size-6 shrink-0 place-items-center rounded-full font-mono text-[0.62rem]",
                  done ? "bg-navy text-ivory dark:bg-ivory dark:text-navy" : current ? "bg-accent text-white" : "border border-line-strong text-muted",
                )}
              >
                {done ? <Check className="size-3.5" /> : current ? <Loader2 className="size-3.5 animate-spin" /> : i + 1}
              </span>
              <span className={cn("text-[0.95rem] leading-snug", !done && !current && "text-muted")}>
                {s.goal}
                {s.needs_analysis && <span className="ml-2 chip !py-0 !text-[0.6rem] text-muted">python</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Activity({ turn }: { turn: Turn }) {
  const running = !["done", "error"].includes(turn.phase);
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? running; // expanded while the agents work, collapsed after — unless toggled
  const setOpen = (f: (o: boolean) => boolean) => setUserOpen(f(open));
  const cands = Object.values(turn.candidates);
  const elapsed = turn.metrics?.latency_ms;
  if (!running && turn.activity.length === 0) {
    // Saved conversations don't replay the live log — the stored trace has the full detail.
    return turn.runId ? (
      <Link href={`/runs/${turn.runId}`} className="flex items-center gap-2 px-1 font-mono text-xs uppercase tracking-[0.16em] text-muted hover:text-ink">
        <Check className="size-3.5" /> Agent activity{elapsed ? ` · ${fmtMs(elapsed)}` : ""} · open the trace
      </Link>
    ) : null;
  }
  return (
    <div className="px-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex items-center gap-2 text-xs text-muted hover:text-ink">
        {running ? <Sparkles className="size-3.5 text-accent" /> : <Check className="size-3.5" />}
        <span className="font-mono uppercase tracking-[0.16em]">
          {running ? "Agents at work" : `Agent activity · ${turn.activity.length} steps${elapsed ? ` · ${fmtMs(elapsed)}` : ""}`}
        </span>
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="mt-3 space-y-1.5 border-l border-line pl-4" aria-live="polite" aria-relevant="additions text">
          {turn.activity.map((a, i) => {
            const last = i === turn.activity.length - 1 && running;
            return (
              <li key={i} className={cn("relative text-[0.85rem]", last ? "text-ink" : "text-muted")}>
                <span className={cn("absolute -left-[1.3rem] top-[0.45rem] size-2 rounded-full", a.status === "error" || a.status === "blocked" ? "bg-warn" : last ? "bg-accent [animation:dp-pulse-dot_1.2s_ease-in-out_infinite]" : "bg-line-strong")} />
                {a.label}
              </li>
            );
          })}
          {running && turn.activity.length === 0 && (
            <li className="text-[0.85rem] text-muted">
              Connecting <Dots className="ml-1" />
            </li>
          )}
        </ol>
      )}
      {running && cands.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 pl-4">
          {cands.map((c) => (
            <span key={c.id} className={cn("chip", c.status === "ok" ? "border-positive/30 text-positive" : c.status === "error" || c.status === "rejected" ? "border-warn/40 text-warn" : "text-muted")}>
              {c.status === "ok" ? <Check className="size-3" /> : c.status === "running" || c.status === "repairing" ? <Loader2 className="size-3 animate-spin" /> : <CircleDashed className="size-3" />}
              {STRATEGY_LABEL[c.strategy] ?? c.strategy}
              {c.row_count != null && <span className="font-mono text-[0.62rem] opacity-70">{fmtInt(c.row_count)} rows</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Clarify({ turn, onChoose }: { turn: Turn; onChoose: (c: string) => void }) {
  const [other, setOther] = useState("");
  const [showOther, setShowOther] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => first.current?.focus(), []);
  if (!turn.clarify) return null;
  return (
    <div className="card relative overflow-hidden p-6" role="group" aria-label="Clarifying question">
      <span className="absolute inset-y-0 left-0 w-1 bg-accent" />
      <p className="label !text-accent">DataPilot asks</p>
      <p className="mt-2 font-serif text-2xl leading-snug">{turn.clarify.question}</p>
      <div className="mt-5 flex flex-wrap gap-2">
        {turn.clarify.options.map((o, i) => (
          <button key={o} ref={i === 0 ? first : undefined} type="button" onClick={() => onChoose(o)} className="btn btn-ghost !py-2">
            {o}
          </button>
        ))}
        <button type="button" onClick={() => setShowOther(true)} className="btn !py-2 text-muted hover:text-ink">
          Something else…
        </button>
      </div>
      {showOther && (
        <form
          className="mt-4 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (other.trim()) onChoose(other.trim());
          }}
        >
          <label htmlFor={`other-${turn.key}`} className="sr-only">Describe what you mean</label>
          <input id={`other-${turn.key}`} autoFocus value={other} onChange={(e) => setOther(e.target.value)} maxLength={300} className="input !py-2" placeholder="Describe what you mean" />
          <button type="submit" className="btn btn-primary !py-2">Send</button>
        </form>
      )}
    </div>
  );
}

function Confirm({ turn, onAction }: { turn: Turn; onAction: (a: "run" | "cancel" | "narrow") => void }) {
  const [showSql, setShowSql] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => first.current?.focus(), []);
  const c = turn.confirm;
  if (!c) return null;
  return (
    <div className="card relative overflow-hidden p-6" role="alertdialog" aria-labelledby={`cf-${turn.key}`} aria-describedby={`cfd-${turn.key}`}>
      <span className="absolute inset-y-0 left-0 w-1 bg-accent" />
      <div className="flex items-start gap-4">
        <ScanSearch className="mt-1 size-6 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <p id={`cf-${turn.key}`} className="font-serif text-2xl">Run this query?</p>
          <p id={`cfd-${turn.key}`} className="mt-2 text-[0.95rem] text-ink-2">
            It will scan about <b>{fmtInt(c.scanned_rows)}</b> rows in <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-sm">{c.table}</code>
            {c.unindexed_join ? ", including a join without an index" : ""}. Estimated time: {c.seconds}.
          </p>
          <button type="button" onClick={() => setShowSql((s) => !s)} aria-expanded={showSql} className="mt-3 flex items-center gap-1 text-xs font-semibold text-muted hover:text-ink">
            SQL <ChevronDown className={cn("size-3.5 transition-transform", showSql && "rotate-180")} />
          </button>
          {showSql && <CodeBlock code={c.sql} lang="sql" className="mt-2" />}
          <div className="mt-5 flex flex-wrap gap-2">
            <button type="button" onClick={() => onAction("cancel")} className="btn btn-ghost !py-2">Cancel</button>
            <button type="button" onClick={() => onAction("narrow")} className="btn btn-ghost !py-2">Narrow it down</button>
            <button ref={first} type="button" onClick={() => onAction("run")} className="btn btn-crimson !py-2">Run anyway</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function TurnView({
  turn,
  dbId,
  index,
  onClarify,
  onConfirm,
  onFeedback,
  onRunAgain,
}: {
  turn: Turn;
  dbId: string;
  index: number;
  onClarify: (c: string) => void;
  onConfirm: (a: "run" | "cancel" | "narrow") => void;
  onFeedback: (v: 1 | -1) => void;
  onRunAgain: () => void;
}) {
  const showAnswer = turn.answer || turn.tables.length || turn.phase === "done";
  const err = turn.error;
  const fatal = err && (turn.phase === "error" || ["blocked", "quota_exhausted", "rate_limited", "backend_starting"].includes(err.code));
  return (
    <section className="space-y-4" aria-label={`Question ${index + 1}`}>
      <div className="flex items-baseline gap-4">
        <span className="font-serif text-lg italic text-accent">{String(index + 1).padStart(2, "0")}</span>
        <h2 className="display text-[1.7rem] leading-tight sm:text-[2rem]">{turn.question}</h2>
      </div>
      <div className="space-y-4 sm:pl-10">
        <PlanSteps turn={turn} />
        <Activity turn={turn} />
        {turn.phase === "clarify" && <Clarify turn={turn} onChoose={onClarify} />}
        {turn.phase === "confirm" && <Confirm turn={turn} onAction={onConfirm} />}
        {fatal ? (
          <div className="flex items-start gap-3 rounded-2xl border border-warn/40 bg-warn-soft px-5 py-4 text-sm text-warn" role="alert">
            <AlertOctagon className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-semibold">{err.code === "blocked" ? "Blocked by the input guard" : "Couldn't finish this one"}</p>
              <p className="mt-1">{ERROR_COPY[err.code] ?? err.message}</p>
              {turn.phase === "error" && (
                <button type="button" onClick={onRunAgain} className="mt-3 font-semibold underline">Try again</button>
              )}
            </div>
          </div>
        ) : showAnswer ? (
          <AnswerCard turn={turn} dbId={dbId} onFeedback={onFeedback} onRunAgain={onRunAgain} />
        ) : null}
      </div>
    </section>
  );
}
