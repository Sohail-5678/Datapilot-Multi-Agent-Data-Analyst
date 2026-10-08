"use client";

/**
 * Main-thread side of the Pyodide sandbox (SPEC §4.2, §8.1): one dedicated worker, preloaded when the page is
 * idle, a 10 s hard timeout on user code (the worker is terminated and recreated), and size limits on input.
 */
export interface SandboxResult {
  ok: boolean;
  result?: unknown;
  stdout?: string;
  error?: string;
  duration_ms: number;
}

const WORKER_URL = "/sandbox/pyodide-worker.js";
const MAX_ROWS = 5000;
const MAX_BYTES = 5_000_000;
const LOAD_TIMEOUT_MS = 90_000;

type Pending = { resolve: (r: SandboxResult) => void; timer?: ReturnType<typeof setTimeout>; started: boolean };

class Sandbox {
  private worker: Worker | null = null;
  private ready: Promise<number> | null = null;
  private pending = new Map<string, Pending>();
  status: "idle" | "loading" | "ready" | "error" = "idle";
  private listeners = new Set<() => void>();

  onStatus(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private setStatus(s: Sandbox["status"]) {
    this.status = s;
    this.listeners.forEach((f) => f());
  }

  private spawn() {
    const w = new Worker(WORKER_URL, { name: "datapilot-sandbox" });
    w.onmessage = (ev: MessageEvent) => this.onMessage(ev.data);
    w.onerror = () => this.failAll("The sandbox worker crashed.");
    this.worker = w;
    this.ready = new Promise<number>((resolve, reject) => {
      const t = setTimeout(() => reject(new Error("Sandbox took too long to load.")), LOAD_TIMEOUT_MS);
      const onReady = (ev: MessageEvent) => {
        if (ev.data?.type === "ready") {
          clearTimeout(t);
          w.removeEventListener("message", onReady);
          resolve(ev.data.load_ms as number);
        }
      };
      w.addEventListener("message", onReady);
      w.postMessage({ type: "init" });
    });
    this.setStatus("loading");
    this.ready.then(
      () => this.setStatus("ready"),
      () => this.setStatus("error"),
    );
  }

  /** Warm up in the background (called after the page is idle). */
  preload() {
    if (!this.worker) this.spawn();
    return this.ready!;
  }

  private onMessage(msg: { type: string; id?: string } & Partial<SandboxResult>) {
    if (!msg.id) return;
    const p = this.pending.get(msg.id);
    if (!p) return;
    if (msg.type === "started") {
      p.started = true;
      return;
    }
    if (msg.type === "result") {
      clearTimeout(p.timer);
      this.pending.delete(msg.id);
      p.resolve({ ok: !!msg.ok, result: msg.result, stdout: msg.stdout, error: msg.error, duration_ms: msg.duration_ms ?? 0 });
    }
  }

  private failAll(error: string) {
    for (const [id, p] of this.pending) {
      clearTimeout(p.timer);
      p.resolve({ ok: false, error, duration_ms: 0 });
      this.pending.delete(id);
    }
    this.kill();
  }

  private kill() {
    this.worker?.terminate();
    this.worker = null;
    this.ready = null;
    this.setStatus("idle");
  }

  async run(code: string, data: { columns: string[]; rows: unknown[][] }, timeoutMs = 10_000): Promise<SandboxResult> {
    const size = JSON.stringify(data).length;
    if (data.rows.length > MAX_ROWS || size > MAX_BYTES) {
      return { ok: false, error: `Data too large for the sandbox (${data.rows.length} rows; max ${MAX_ROWS} rows / 5 MB).`, duration_ms: 0 };
    }
    try {
      await this.preload();
    } catch (e) {
      this.kill();
      return { ok: false, error: e instanceof Error ? e.message : "Sandbox failed to load.", duration_ms: 0 };
    }
    const id = crypto.randomUUID();
    return new Promise<SandboxResult>((resolve) => {
      const p: Pending = { resolve, started: false };
      this.pending.set(id, p);
      // Hard timeout: prelude + user code. Infinite loops can't be interrupted, so the worker is terminated.
      p.timer = setTimeout(() => {
        this.pending.delete(id);
        this.kill();
        resolve({ ok: false, error: `Timed out after ${timeoutMs / 1000} s — the sandbox was stopped.`, duration_ms: timeoutMs });
      }, timeoutMs + 3000);
      this.worker!.postMessage({ type: "run", id, code, data });
    });
  }
}

let instance: Sandbox | null = null;
export function getSandbox() {
  if (!instance) instance = new Sandbox();
  return instance;
}
