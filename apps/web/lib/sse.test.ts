import { describe, expect, it } from "vitest";
import { parseFrame, readSse, type SseEvent } from "@/lib/sse";

describe("parseFrame", () => {
  it("parses id, event and JSON data", () => {
    expect(parseFrame('id: 7\nevent: plan\ndata: {"steps":[]}')).toEqual({ id: 7, event: "plan", data: { steps: [] } });
  });
  it("ignores heartbeat comments and retry lines", () => {
    expect(parseFrame(": heartbeat")).toBeNull();
    expect(parseFrame("retry: 3000")).toBeNull();
  });
  it("returns null for invalid JSON", () => {
    expect(parseFrame("event: x\ndata: {nope")).toBeNull();
  });
});

describe("readSse", () => {
  it("handles frames split across chunks", async () => {
    const chunks = ['id: 1\nevent: run\ndata: {"run_id":"r', '1"}\n\n: heartbeat\n\nid: 2\nevent: done\ndata: {"status":"success"}\n\n'];
    const stream = new ReadableStream({
      start(c) {
        for (const ch of chunks) c.enqueue(new TextEncoder().encode(ch));
        c.close();
      },
    });
    const got: SseEvent[] = [];
    await readSse(new Response(stream, { status: 200 }), (e) => got.push(e));
    expect(got.map((e) => e.event)).toEqual(["run", "done"]);
    expect(got[0].data.run_id).toBe("r1");
  });
  it("throws an ApiError for non-2xx responses", async () => {
    const res = new Response(JSON.stringify({ error: { code: "rate_limited", message: "slow down" } }), { status: 429, headers: { "content-type": "application/json", "retry-after": "12" } });
    await expect(readSse(res, () => {})).rejects.toMatchObject({ code: "rate_limited", retryAfter: 12 });
  });
});
