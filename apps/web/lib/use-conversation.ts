"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { readSse } from "@/lib/sse";
import { applyEvent, newTurn, turnFromRun, type Turn } from "@/lib/turn";
import type { RunRecord, ThreadInfo } from "@/lib/types";
import { getSandbox } from "@/sandbox/runner";

const MAX_RECONNECTS = 6;

/**
 * Drives one conversation: creates the thread, streams each run's SSE events into a Turn, re-attaches when the
 * stream drops (serverless proxies have a max duration), answers sandbox requests in the browser, and posts
 * clarify/confirm choices back to the paused run.
 */
export function useConversation(dbId: string, initialThreadId?: string | null) {
  const [threadId, setThreadId] = useState<string | null>(initialThreadId ?? null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(Boolean(initialThreadId));
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [threadDb, setThreadDb] = useState<string | null>(null);
  const aborters = useRef(new Map<string, AbortController>());
  const handledSandbox = useRef(new Set<string>());

  const update = useCallback((key: string, fn: (t: Turn) => Turn) => {
    setTurns((ts) => ts.map((t) => (t.key === key ? fn(t) : t)));
  }, []);

  // Load a saved conversation.
  useEffect(() => {
    if (!initialThreadId) return;
    let cancelled = false;
    api<{ thread: ThreadInfo; runs: RunRecord[] }>(`threads/${initialThreadId}`)
      .then((d) => {
        if (cancelled) return;
        setThreadDb(d.thread.db_id);
        setTurns(d.runs.map((r) => turnFromRun(r)));
      })
      .catch((e) => !cancelled && setLoadError(e instanceof ApiError ? e : new ApiError(500, "internal", String(e))))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [initialThreadId]);

  useEffect(() => {
    const map = aborters.current;
    return () => map.forEach((a) => a.abort());
  }, []);

  const runSandbox = useCallback(
    async (key: string, runId: string, req: NonNullable<Turn["sandbox"]>) => {
      if (handledSandbox.current.has(req.request_id)) return;
      handledSandbox.current.add(req.request_id);
      let body: { request_id: string; ok: boolean; result?: unknown; stdout?: string; error?: string; duration_ms: number };
      try {
        const data = await api<{ columns: string[]; rows: unknown[][] }>(`runs/${runId}/data/${req.data_ref}`);
        const res = await getSandbox().run(req.code, data, req.timeout_ms || 10_000);
        body = { request_id: req.request_id, ok: res.ok, result: res.result, stdout: res.stdout, error: res.error, duration_ms: res.duration_ms };
      } catch (e) {
        body = { request_id: req.request_id, ok: false, error: e instanceof Error ? e.message : "Sandbox failed.", duration_ms: 0 };
      }
      update(key, (t) => ({ ...t, analysis: { ...body, code: req.code, ran_in: "browser", rows: req.rows } }));
      await api(`runs/${runId}/sandbox_result`, { json: body }).catch(() => {});
    },
    [update],
  );

  const follow = useCallback(
    async (key: string, first: () => Promise<Response>) => {
      const ctl = new AbortController();
      aborters.current.set(key, ctl);
      let finished = false;
      let runId: string | null = null;
      let lastId = 0;
      const onEvent = (e: { id: number; event: string; data: Record<string, unknown> }) => {
        lastId = Math.max(lastId, e.id);
        if (e.event === "run") runId = String(e.data.run_id);
        if (e.event === "done") finished = true;
        update(key, (t) => applyEvent(t, e.event, e.data, e.id));
        if (e.event === "sandbox_request" && runId) void runSandbox(key, runId, e.data as unknown as NonNullable<Turn["sandbox"]>);
      };
      try {
        await readSse(await first(), onEvent, ctl.signal);
        for (let attempt = 0; !finished && runId && attempt < MAX_RECONNECTS && !ctl.signal.aborted; attempt++) {
          await new Promise((r) => setTimeout(r, Math.min(4000, 500 * 2 ** attempt)));
          try {
            const res = await fetch(`/api/v1/runs/${runId}/events?after=${lastId}`, { signal: ctl.signal, cache: "no-store" });
            if (res.status === 404) break;
            await readSse(res, onEvent, ctl.signal);
            attempt = -1; // a successful re-attach resets the backoff
          } catch (err) {
            if (ctl.signal.aborted) break;
            if (err instanceof ApiError && err.status < 500) break;
          }
        }
        if (!finished && !ctl.signal.aborted) {
          if (runId) {
            // Stream gone for good: fall back to the stored run.
            try {
              const r = await api<RunRecord>(`runs/${runId}`);
              if (!r.live) update(key, (t) => ({ ...turnFromRun(r), key: t.key, tables: t.tables.length ? t.tables : turnFromRun(r).tables }));
            } catch {
              update(key, (t) => ({ ...t, phase: "error", error: { code: "internal", message: "Lost the connection to this run." } }));
            }
          } else {
            update(key, (t) => ({ ...t, phase: "error", error: { code: "internal", message: "The server closed the connection." } }));
          }
        }
      } catch (err) {
        const e = err instanceof ApiError ? err : new ApiError(0, "network", "Network error — check your connection.");
        update(key, (t) => ({ ...t, phase: "error", error: { code: e.code, message: e.message } }));
      } finally {
        aborters.current.delete(key);
      }
    },
    [runSandbox, update],
  );

  const ask = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q) return;
      const turn = newTurn(q);
      setTurns((ts) => [...ts, turn]);
      let tid = threadId;
      try {
        if (!tid) {
          const r = await api<{ thread_id: string }>("threads", { json: { db_id: dbId } });
          tid = r.thread_id;
          setThreadId(tid);
          setThreadDb(dbId);
          window.history.replaceState(null, "", `/app/${tid}`);
        }
      } catch (err) {
        const e = err instanceof ApiError ? err : new ApiError(0, "network", "Network error.");
        update(turn.key, (t) => ({ ...t, phase: "error", error: { code: e.code, message: e.message } }));
        return;
      }
      await follow(turn.key, () =>
        fetch(`/api/v1/threads/${tid}/ask`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ question: q }),
          cache: "no-store",
        }),
      );
    },
    [dbId, follow, threadId, update],
  );

  const answerClarify = useCallback(
    async (turn: Turn, choice: string) => {
      if (!turn.runId) return;
      update(turn.key, (t) => ({ ...t, phase: "running", clarify: null, activity: [...t.activity, { node: "clarify", label: `You chose: ${choice}`, status: "done", at: Date.now() }] }));
      await api(`runs/${turn.runId}/clarify`, { json: { choice } }).catch((e: ApiError) =>
        update(turn.key, (t) => ({ ...t, error: { code: e.code, message: e.message } })),
      );
    },
    [update],
  );

  const answerConfirm = useCallback(
    async (turn: Turn, action: "run" | "cancel" | "narrow") => {
      if (!turn.runId) return;
      update(turn.key, (t) => ({ ...t, phase: "running", confirm: null }));
      await api(`runs/${turn.runId}/confirm`, { json: { action } }).catch((e: ApiError) =>
        update(turn.key, (t) => ({ ...t, error: { code: e.code, message: e.message } })),
      );
    },
    [update],
  );

  const sendFeedback = useCallback(
    async (turn: Turn, thumbs: 1 | -1) => {
      if (!turn.runId) return;
      update(turn.key, (t) => ({ ...t, feedback: thumbs }));
      await api(`runs/${turn.runId}/feedback`, { json: { thumbs } }).catch(() => {});
    },
    [update],
  );

  const busy = turns.some((t) => !["done", "error"].includes(t.phase));
  return { threadId, threadDb, turns, ask, answerClarify, answerConfirm, sendFeedback, busy, loading, loadError };
}
