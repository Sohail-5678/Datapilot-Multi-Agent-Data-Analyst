import { describe, expect, it } from "vitest";
import { normalizeSummary } from "@/lib/bench";

describe("bench summary", () => {
  it("is unavailable when no config has qualified", () => {
    const s = normalizeSummary({ configs: {}, note: "No qualifying benchmark run has been recorded yet." });
    expect(s.available).toBe(false);
    expect(s.note).toMatch(/No qualifying/);
  });
  it("maps harness rows to table rows", () => {
    const s = normalizeSummary({
      configs: { C0: { config: "C0", n: 50, ex: 0.42, ex_ci95: [0.3, 0.56], cost_per_question_usd: 0.0009, latency_p50_ms: 1400, latency_p95_ms: 3000, preview: false } },
      per_difficulty: { C0: { simple: { n: 14, ex: 0.6 } } },
    });
    expect(s.available).toBe(true);
    expect(s.configs[0]).toMatchObject({ config: "C0", ex: 0.42, ci_low: 0.3, ci_high: 0.56, p50_ms: 1400 });
    expect(s.configs[0].per_difficulty?.simple.ex).toBe(0.6);
  });
});
