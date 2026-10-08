"use client";

import { toError } from "@/lib/api";

export interface SseEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

/**
 * Minimal SSE client over fetch (EventSource can't POST). Parses `id:`/`event:`/`data:` frames,
 * ignores heartbeat comments, and resolves when the stream ends.
 */
export async function readSse(res: Response, onEvent: (e: SseEvent) => void, signal?: AbortSignal) {
  if (!res.ok) throw await toError(res);
  if (!res.body) throw new Error("No response body");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const onAbort = () => reader.cancel().catch(() => {});
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + (buf[idx] === "\r" ? 4 : 2));
        const ev = parseFrame(frame);
        if (ev) onEvent(ev);
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

export function parseFrame(frame: string): SseEvent | null {
  let id = 0;
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const i = line.indexOf(":");
    const field = i >= 0 ? line.slice(0, i) : line;
    const value = i >= 0 ? line.slice(i + 1).replace(/^ /, "") : "";
    if (field === "id") id = Number(value) || 0;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  try {
    return { id, event, data: JSON.parse(data.join("\n")) };
  } catch {
    return null;
  }
}
