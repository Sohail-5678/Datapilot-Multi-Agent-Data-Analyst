import summary from "@/data/bench-summary.json";

/**
 * Benchmark summary written by the harness (`python -m datapilot.bench --summarize` → bench/summary.json, copied
 * here by `pnpm sync:bench`). Never fabricated: configs that haven't run (or have n < 20) are simply absent.
 */
export interface BenchConfigRow {
  config: string;
  label?: string;
  n: number;
  ex: number;
  ci_low?: number;
  ci_high?: number;
  tokens_per_q?: number;
  cost_per_q?: number;
  llm_calls_per_q?: number;
  p50_ms?: number;
  p95_ms?: number;
  preview?: boolean;
  split?: string;
  per_difficulty?: Record<string, { n: number; ex: number }>;
  per_db?: Record<string, { n: number; ex: number }>;
}

export interface BenchSummary {
  available: boolean;
  updated_at?: string;
  note?: string;
  configs: BenchConfigRow[];
  failed_cases?: { question_id: string | number; db_id: string; difficulty: string; question: string; gold_sql: string; pred_sql: string | null; config: string; error?: string | null }[];
}

interface RawConfig {
  config: string;
  label?: string;
  split?: string;
  n: number;
  preview?: boolean;
  ex: number | null;
  ex_ci95?: [number, number] | null;
  llm_calls_per_question?: number | null;
  tokens_per_question?: number | null;
  cost_per_question_usd?: number | null;
  latency_p50_ms?: number | null;
  latency_p95_ms?: number | null;
}

interface RawSummary {
  generated_at?: string;
  note?: string;
  preview?: boolean;
  configs?: Record<string, RawConfig>;
  per_difficulty?: Record<string, Record<string, { n: number; ex: number }>>;
  per_db?: Record<string, Record<string, { n: number; ex: number }>>;
  failed_cases?: BenchSummary["failed_cases"];
}

export function normalizeSummary(raw: RawSummary): BenchSummary {
  const configs = Object.values(raw.configs ?? {})
    .filter((c) => c && typeof c.ex === "number" && c.n > 0)
    .map<BenchConfigRow>((c) => ({
      config: c.config,
      label: c.label,
      split: c.split,
      n: c.n,
      preview: c.preview,
      ex: c.ex as number,
      ci_low: c.ex_ci95?.[0],
      ci_high: c.ex_ci95?.[1],
      llm_calls_per_q: c.llm_calls_per_question ?? undefined,
      tokens_per_q: c.tokens_per_question ?? undefined,
      cost_per_q: c.cost_per_question_usd ?? undefined,
      p50_ms: c.latency_p50_ms ?? undefined,
      p95_ms: c.latency_p95_ms ?? undefined,
      per_difficulty: raw.per_difficulty?.[c.config],
      per_db: raw.per_db?.[c.config],
    }))
    .sort((a, b) => a.config.localeCompare(b.config));
  const previewNote = configs.some((c) => c.preview) ? "Preview: at least one configuration hasn't finished all planned questions yet; intervals are wide." : undefined;
  return {
    available: configs.length > 0,
    updated_at: raw.generated_at,
    note: configs.length ? previewNote : raw.note,
    configs,
    failed_cases: raw.failed_cases ?? [],
  };
}

export function benchSummary(): BenchSummary {
  return normalizeSummary(summary as unknown as RawSummary);
}
