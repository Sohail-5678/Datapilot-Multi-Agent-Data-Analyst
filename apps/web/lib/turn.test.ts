import { describe, expect, it } from "vitest";
import { applyEvent, newTurn, type Turn } from "@/lib/turn";

function run(events: [string, Record<string, unknown>][]): Turn {
  return events.reduce((t, [e, d], i) => applyEvent(t, e, d, i + 1), newTurn("q"));
}

describe("turn reducer", () => {
  it("builds a finished answer from the event stream", () => {
    const t = run([
      ["run", { run_id: "r1", fake_llm: true }],
      ["plan", { steps: [{ goal: "g", needs_sql: true, needs_analysis: false }] }],
      ["candidate", { id: "c1-direct", strategy: "direct", status: "ok", row_count: 5 }],
      ["chosen", { step_idx: 0, sql: "SELECT 1", confidence: "High", reasons: ["2 of 2 candidates agree"], agree: 2, of: 2 }],
      ["table", { data_ref: "r1-x", columns: ["a"], rows: [[1]], row_count: 1, truncated: false, step_idx: 0 }],
      ["step", { node: "advance", status: "done", step_idx: 0 }],
      ["grounding", { checked: 2, removed: [], ok: true }],
      ["token", { text: "Rock leads " }],
      ["token", { text: "with $10." }],
      ["done", { status: "success", confidence: "High", metrics: { llm_calls: 6 } }],
    ]);
    expect(t.runId).toBe("r1");
    expect(t.fake).toBe(true);
    expect(t.phase).toBe("done");
    expect(t.answer).toBe("Rock leads with $10.");
    expect(t.confidence).toBe("High");
    expect(t.tables).toHaveLength(1);
    expect(t.stepsDone).toBe(1);
    expect(t.candidates).toEqual({});
  });

  it("enters and leaves the clarify checkpoint", () => {
    let t = run([["run", { run_id: "r" }], ["clarify_request", { question: "Which?", options: ["A", "B"] }]]);
    expect(t.phase).toBe("clarify");
    expect(t.clarify?.options).toEqual(["A", "B"]);
    t = applyEvent(t, "step", { node: "planner", status: "running", label: "Planning…" }, 9);
    expect(t.phase).toBe("running");
  });

  it("records the confirm request and sandbox request", () => {
    const t = run([["run", { run_id: "r" }], ["confirm_request", { sql: "SELECT", scanned_rows: 183978, table: "Player_Attributes", seconds: "3–5 s" }]]);
    expect(t.phase).toBe("confirm");
    expect(t.confirm?.scanned_rows).toBe(183978);
    const s = applyEvent(t, "sandbox_request", { request_id: "x", code: "result=1", data_ref: "d", data_url: "/v1/runs/r/data/d", rows: 3, timeout_ms: 10000 });
    expect(s.phase).toBe("sandbox");
  });

  it("marks internal errors as failed turns", () => {
    const t = run([["error", { code: "internal", message: "boom" }], ["done", { status: "error" }]]);
    expect(t.phase).toBe("error");
    expect(t.error?.message).toBe("boom");
  });
});
