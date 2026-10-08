import type { AnalysisInfo, CandidateInfo, ChartInfo, ChosenInfo, Grounding, Metrics, PlanStep, RunRecord, TableResult } from "@/lib/types";

/** One question → answer exchange, built from SSE events (live) or a stored run (history). */
export interface Activity {
  node: string;
  label: string;
  status: string;
  at: number;
}

export interface Turn {
  key: string;
  runId: string | null;
  question: string;
  phase: "starting" | "running" | "clarify" | "confirm" | "sandbox" | "done" | "error";
  plan: PlanStep[];
  stepIdx: number;
  stepsDone: number;
  activity: Activity[];
  candidates: Record<string, CandidateInfo>;
  chosen: ChosenInfo[];
  tables: TableResult[];
  chart: ChartInfo | null;
  answer: string;
  grounding: Grounding | null;
  clarify: { question: string; options: string[] } | null;
  confirm: { sql: string; scanned_rows: number; table: string; seconds: string; unindexed_join?: boolean } | null;
  sandbox: { request_id: string; code: string; data_ref: string; data_url: string; rows: number; timeout_ms: number } | null;
  analysis: AnalysisInfo | null;
  status: string | null;
  confidence: string | null;
  metrics: Metrics | null;
  notes: string[];
  error: { code: string; message: string } | null;
  fake: boolean;
  lastEventId: number;
  startedAt: number;
  feedback: number | null;
}

export function newTurn(question: string): Turn {
  return {
    key: crypto.randomUUID(),
    runId: null,
    question,
    phase: "starting",
    plan: [],
    stepIdx: 0,
    stepsDone: 0,
    activity: [],
    candidates: {},
    chosen: [],
    tables: [],
    chart: null,
    answer: "",
    grounding: null,
    clarify: null,
    confirm: null,
    sandbox: null,
    analysis: null,
    status: null,
    confidence: null,
    metrics: null,
    notes: [],
    error: null,
    fake: false,
    lastEventId: 0,
    startedAt: Date.now(),
    feedback: null,
  };
}

type Data = Record<string, unknown>;

export function applyEvent(t: Turn, event: string, d: Data, id = 0): Turn {
  const next: Turn = { ...t, lastEventId: Math.max(t.lastEventId, id) };
  const act = (label: string, node: string, status = "running") => {
    const last = next.activity[next.activity.length - 1];
    if (last && last.node === node && last.label.split("…")[0] === label.split("…")[0]) {
      next.activity = [...next.activity.slice(0, -1), { node, label, status, at: Date.now() }];
    } else {
      next.activity = [...next.activity, { node, label, status, at: Date.now() }];
    }
  };
  switch (event) {
    case "run":
      next.runId = String(d.run_id);
      next.fake = Boolean(d.fake_llm);
      next.phase = "running";
      break;
    case "plan":
      next.plan = (d.steps as PlanStep[]) ?? [];
      act(`Plan ready · ${next.plan.length} step${next.plan.length === 1 ? "" : "s"}`, "planner", "done");
      break;
    case "step": {
      const node = String(d.node ?? "");
      if (node === "advance") {
        next.stepsDone = Number(d.step_idx ?? 0) + 1;
        next.stepIdx = next.stepsDone;
      } else {
        act(String(d.label ?? node), node, String(d.status ?? "running"));
      }
      if (next.phase === "clarify" || next.phase === "confirm" || next.phase === "sandbox") next.phase = "running";
      break;
    }
    case "candidate": {
      const c = d as unknown as CandidateInfo;
      next.candidates = { ...next.candidates, [c.id]: { ...next.candidates[c.id], ...c } };
      // Per-candidate progress lives in the chips; only repairs and failures earn a line in the log.
      if (c.status === "repairing") act(`Repairing ${c.strategy.replace(/_/g, " ")} (attempt ${d.attempt ?? 1})…`, `repair-${c.id}`);
      else if (c.status === "error" || c.status === "rejected") act(`A ${c.strategy.replace(/_/g, " ")} candidate failed`, `fail-${c.id}`, "error");
      break;
    }
    case "chosen": {
      const c = d as unknown as ChosenInfo;
      next.chosen = [...next.chosen.filter((x) => x.step_idx !== c.step_idx), c];
      next.confidence = c.confidence;
      act(`${c.agree} of ${c.of} agree → verified`, "verifier", "done");
      next.candidates = {};
      break;
    }
    case "table": {
      const tr = d as unknown as TableResult;
      next.tables = [...next.tables.filter((x) => x.step_idx !== tr.step_idx), tr];
      break;
    }
    case "chart":
      next.chart = d as unknown as ChartInfo;
      break;
    case "clarify_request":
      next.phase = "clarify";
      next.clarify = { question: String(d.question ?? "Which one do you mean?"), options: (d.options as string[]) ?? [] };
      break;
    case "confirm_request":
      next.phase = "confirm";
      next.confirm = d as unknown as Turn["confirm"];
      break;
    case "sandbox_request":
      next.phase = "sandbox";
      next.sandbox = d as unknown as Turn["sandbox"];
      act("Running the analysis in your browser…", "sandbox_call");
      break;
    case "token":
      next.answer = next.answer + String(d.text ?? "");
      break;
    case "grounding":
      next.grounding = d as unknown as Grounding;
      break;
    case "error":
      next.error = { code: String(d.code ?? "internal"), message: String(d.message ?? "Something went wrong.") };
      break;
    case "done":
      next.phase = next.error && next.error.code === "internal" ? "error" : "done";
      next.status = String(d.status ?? "done");
      next.metrics = (d.metrics as Metrics) ?? null;
      next.notes = (d.notes as string[]) ?? [];
      if (typeof d.answer === "string" && d.answer && !next.answer) next.answer = d.answer;
      if (d.confidence) next.confidence = String(d.confidence);
      next.clarify = null;
      next.confirm = null;
      next.sandbox = null;
      break;
  }
  return next;
}

/** Rebuild a finished turn from a stored run record (thread history). */
export function turnFromRun(r: RunRecord, results: Record<string, TableResult | undefined> = {}): Turn {
  const t = newTurn(r.question);
  const detail = r.detail ?? {};
  t.key = r.id;
  t.runId = r.id;
  t.phase = r.status === "running" ? "running" : r.status === "error" ? "error" : "done";
  t.status = r.status;
  t.plan = detail.plan ?? [];
  t.answer = r.answer ?? "";
  t.grounding = r.grounding;
  t.chart = r.chart;
  t.confidence = r.confidence;
  t.metrics = detail.metrics ?? null;
  t.notes = detail.notes ?? [];
  t.analysis = detail.analysis ?? null;
  t.feedback = r.feedback?.thumbs ?? null;
  t.chosen = (detail.steps ?? [])
    .filter((s) => s.ok && s.sql)
    .map((s) => {
      const m = /(\d+) of (\d+)/.exec(s.reasons?.[0] ?? "");
      return { step_idx: s.step_idx, sql: s.sql!, confidence: s.confidence as ChosenInfo["confidence"], reasons: s.reasons, agree: m ? +m[1] : 1, of: m ? +m[2] : 1 };
    });
  t.tables = (detail.steps ?? [])
    .filter((s) => s.ok)
    .map((s) => results[s.data_ref] ?? { data_ref: s.data_ref, columns: s.columns, rows: [], row_count: s.row_count, truncated: s.truncated, step_idx: s.step_idx });
  t.stepsDone = t.plan.length;
  if (r.status === "error") t.error = { code: "internal", message: "This run failed." };
  return t;
}

export const STRATEGY_LABEL: Record<string, string> = {
  direct: "Direct",
  plan_then_sql: "Plan → SQL",
  few_shot: "Few-shot",
  cache: "Verified cache",
};
