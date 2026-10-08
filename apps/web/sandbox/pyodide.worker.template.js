/* DataPilot analysis sandbox — Pyodide (CPython → WebAssembly) in a dedicated Web Worker (SPEC §8.1).
 * GENERATED into public/sandbox/pyodide-worker.js by scripts/sync-sandbox.mjs — edit the template.
 * Before any model-written code runs: network APIs are replaced with throwing, non-configurable stubs
 * (own properties and prototypes), package loading is disabled, and Python imports of js/pyodide/micropip are
 * blocked. The worker has no DOM, cookies or storage. The page terminates it after a 10 s hard timeout. */
"use strict";

const PYODIDE_VERSION = "__PYODIDE_VERSION__";
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PACKAGES = ["numpy", "pandas"];
const LOCKDOWN_PY = __LOCKDOWN_PY__;
const PRELUDE_PY = __PRELUDE_PY__;
const EPILOGUE_PY = __EPILOGUE_PY__;
const MAX_STDOUT = 10000;

let pyodide = null;
let ready = null;
let stdout = "";

function blocked(name) {
  return function () {
    throw new Error(name + " is disabled in the DataPilot sandbox (no network)");
  };
}

function lockdownGlobals(g) {
  const names = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts", "Worker", "SharedWorker",
    "BroadcastChannel", "WebTransport", "RTCPeerConnection", "indexedDB", "caches", "open"];
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
}

async function init() {
  const t0 = performance.now();
  importScripts(INDEX_URL + "pyodide.js");
  pyodide = await self.loadPyodide({
    indexURL: INDEX_URL,
    stdout: (s) => { if (stdout.length < MAX_STDOUT) stdout += s + "\n"; },
    stderr: (s) => { if (stdout.length < MAX_STDOUT) stdout += s + "\n"; },
  });
  await pyodide.loadPackage(PACKAGES, { messageCallback: () => {} });
  pyodide.runPython("import numpy, pandas");
  // From here on: no package loading, no network, no escape hatches.
  pyodide.loadPackage = blocked("loadPackage");
  pyodide.loadPackagesFromImports = blocked("loadPackagesFromImports");
  lockdownGlobals(self);
  pyodide.runPython(LOCKDOWN_PY);
  return Math.round(performance.now() - t0);
}

async function run(id, code, data) {
  const ns = pyodide.globals.get("dict")();
  ns.set("__builtins__", pyodide.globals.get("__builtins__"));
  ns.set("_DP_PAYLOAD", JSON.stringify(data));
  stdout = "";
  try {
    await pyodide.runPythonAsync(PRELUDE_PY, { globals: ns });
    ns.delete("_DP_PAYLOAD");
    self.postMessage({ type: "started", id });
    const t0 = performance.now();
    await pyodide.runPythonAsync(code, { globals: ns });
    const json = await pyodide.runPythonAsync(EPILOGUE_PY, { globals: ns });
    return { ok: true, result: JSON.parse(json), stdout: stdout.slice(0, MAX_STDOUT), duration_ms: Math.round(performance.now() - t0) };
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    return { ok: false, error: msg.split("\n").filter(Boolean).slice(-6).join("\n").slice(0, 2000), stdout: stdout.slice(0, MAX_STDOUT), duration_ms: 0 };
  } finally {
    ns.destroy();
  }
}

self.onmessage = async (ev) => {
  const msg = ev.data || {};
  try {
    if (!ready) ready = init();
    const loadMs = await ready;
    if (msg.type === "init") {
      self.postMessage({ type: "ready", load_ms: loadMs });
      return;
    }
    if (msg.type === "run") {
      const res = await run(msg.id, String(msg.code || ""), msg.data || { columns: [], rows: [] });
      self.postMessage({ type: "result", id: msg.id, ...res });
    }
  } catch (e) {
    self.postMessage({ type: "result", id: msg.id, ok: false, error: "Sandbox failed to start: " + String(e && e.message ? e.message : e) });
  }
};
