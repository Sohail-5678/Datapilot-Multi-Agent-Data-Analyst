#!/usr/bin/env node
// Node.js Pyodide runner for eval/CI mode (SPEC §8.1). Same contract and lockdown as the browser worker:
//   stdin  {code, data: {columns, rows}, timeout_ms?}
//   stdout {ok, result?, stdout, error?, duration_ms, ran_in: "node"}
// The code runs in a worker thread that is terminated after the hard timeout (10 s).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Worker, isMainThread, parentPort, workerData } from "node:worker_threads";

const HERE = dirname(fileURLToPath(import.meta.url));
const MAX_ROWS = 5000;
const MAX_STDOUT = 10_000;
export const PACKAGES = ["numpy", "pandas"];

function blocked(name) {
  return function () {
    throw new Error(`${name} is disabled in the DataPilot sandbox (no network)`);
  };
}

/** Replace network/escape primitives with throwing, non-configurable stubs (own props and prototypes). */
export function lockdownGlobals(g) {
  const names = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts", "Worker", "SharedWorker",
    "BroadcastChannel", "WebTransport", "RTCPeerConnection", "indexedDB", "caches"];
  for (const name of names) {
    const stub = blocked(name);
    let obj = g;
    while (obj) {
      if (Object.prototype.hasOwnProperty.call(obj, name)) {
        try { Object.defineProperty(obj, name, { value: stub, writable: false, configurable: false }); } catch { /* frozen */ }
      }
      obj = Object.getPrototypeOf(obj);
    }
    try { Object.defineProperty(g, name, { value: stub, writable: false, configurable: false }); } catch { /* exists */ }
  }
  if (g.navigator && "sendBeacon" in g.navigator) {
    try { Object.defineProperty(g.navigator, "sendBeacon", { value: blocked("sendBeacon") }); } catch { /* ignore */ }
  }
  if (g.process) {
    // Node-only escape hatches reachable from Python through the `js` proxy.
    for (const fn of ["binding", "_linkedBinding", "dlopen", "getBuiltinModule", "kill", "chdir", "exit", "abort"]) {
      try { Object.defineProperty(g.process, fn, { value: blocked(`process.${fn}`), configurable: false }); } catch { /* ignore */ }
    }
    try { g.process.env = {}; } catch { /* ignore */ }
  }
}

async function runInWorker({ code, data, onStart }) {
  const { loadPyodide } = await import("pyodide");
  let out = "";
  const py = await loadPyodide({
    stdout: (s) => { if (out.length < MAX_STDOUT) out += s + "\n"; },
    stderr: (s) => { if (out.length < MAX_STDOUT) out += s + "\n"; },
  });
  await py.loadPackage(PACKAGES, { messageCallback: () => {} });
  // After packages are in: no more package loading, no network.
  py.loadPackage = blocked("loadPackage");
  py.loadPackagesFromImports = blocked("loadPackagesFromImports");
  py.runPython("import numpy, pandas");  // import before the lockdown (pandas needs ctypes etc. internally)
  lockdownGlobals(globalThis);
  py.runPython(readFileSync(join(HERE, "lockdown.py"), "utf8"));
  py.globals.set("_DP_PAYLOAD", JSON.stringify(data));
  py.runPython(readFileSync(join(HERE, "prelude.py"), "utf8"));
  py.globals.delete("_DP_PAYLOAD");
  onStart?.();
  const t0 = performance.now();
  try {
    await py.runPythonAsync(code);
    const json = py.runPython(readFileSync(join(HERE, "epilogue.py"), "utf8"));
    return { ok: true, result: JSON.parse(json), stdout: out.slice(0, MAX_STDOUT), duration_ms: Math.round(performance.now() - t0) };
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    // Keep the last lines of a Python traceback (the useful part for the analyst's fix attempt).
    const short = msg.split("\n").filter(Boolean).slice(-6).join("\n");
    return { ok: false, error: short.slice(0, 2000), stdout: out.slice(0, MAX_STDOUT), duration_ms: Math.round(performance.now() - t0) };
  }
}

export function runSandboxed(payload, timeoutMs = 10_000) {
  return new Promise((resolve) => {
    const rows = payload?.data?.rows ?? [];
    const size = JSON.stringify(payload?.data ?? {}).length;
    if (rows.length > MAX_ROWS || size > 5_000_000) {
      resolve({ ok: false, error: `Data too large for the sandbox (${rows.length} rows, ${size} bytes; max ${MAX_ROWS} rows / 5 MB).`, duration_ms: 0, ran_in: "node" });
      return;
    }
    const t0 = performance.now();
    const worker = new Worker(fileURLToPath(import.meta.url), { workerData: payload, resourceLimits: { maxOldGenerationSizeMb: 768 } });
    let done = false;
    const finish = (r) => { if (done) return; done = true; clearTimeout(timer); worker.terminate(); resolve({ ...r, ran_in: "node" }); };
    // The timeout covers user code only; Pyodide + packages take a few seconds to load first.
    const loadAllowance = 60_000;
    const timer = setTimeout(() => finish({ ok: false, error: `Timed out after ${timeoutMs / 1000} s (worker terminated).`, duration_ms: Math.round(performance.now() - t0) }), loadAllowance + timeoutMs);
    worker.on("message", (m) => {
      if (m.type === "started") {
        clearTimeout(timer);
        setTimeout(() => finish({ ok: false, error: `Timed out after ${timeoutMs / 1000} s (worker terminated).`, duration_ms: timeoutMs }), timeoutMs);
      } else if (m.type === "result") finish(m.result);
    });
    worker.on("error", (e) => finish({ ok: false, error: String(e), duration_ms: Math.round(performance.now() - t0) }));
    worker.on("exit", (c) => finish({ ok: false, error: `Sandbox exited (code ${c}).`, duration_ms: Math.round(performance.now() - t0) }));
  });
}

if (!isMainThread) {
  // "started" is posted just before user code, so the parent's 10 s clock excludes Pyodide load time.
  runInWorker({ ...workerData, onStart: () => parentPort.postMessage({ type: "started" }) })
    .then((result) => parentPort.postMessage({ type: "result", result }))
    .catch((e) => parentPort.postMessage({ type: "result", result: { ok: false, error: String(e) } }));
} else if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const input = JSON.parse(readFileSync(0, "utf8"));
  const res = await runSandboxed(input, input.timeout_ms ?? 10_000);
  process.stdout.write(JSON.stringify(res));
}
