/* DataPilot analysis sandbox — Pyodide (CPython → WebAssembly) in a dedicated Web Worker (SPEC §8.1).
 * GENERATED into public/sandbox/pyodide-worker.js by scripts/sync-sandbox.mjs — edit the template.
 * Before any model-written code runs: network APIs are replaced with throwing, non-configurable stubs
 * (own properties and prototypes), package loading is disabled, and Python imports of js/pyodide/micropip are
 * blocked. The worker has no DOM, cookies or storage. The page terminates it after a 10 s hard timeout. */
"use strict";

const PYODIDE_VERSION = "314.0.7";
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PACKAGES = ["numpy", "pandas"];
const LOCKDOWN_PY = "# Python-side lockdown shared by the browser worker and the Node runner (SPEC §8.1).\n# Runs after pandas/numpy/scipy are loaded and before any model-written code.\n# Defense in depth only: the real boundary is WebAssembly + a worker whose network APIs were replaced.\nimport sys\n\n_BLOCKED = (\"js\", \"pyodide_js\", \"pyodide\", \"pyodide.code\", \"pyodide.ffi\", \"pyodide.http\", \"micropip\",\n            \"_pyodide\", \"_pyodide_core\", \"socket\", \"ssl\", \"urllib.request\", \"http.client\", \"subprocess\")\n\n\nclass _BlockImports:\n    def find_spec(self, name, path=None, target=None):\n        if name in _BLOCKED or name.split(\".\")[0] in (\"js\", \"pyodide_js\", \"pyodide\", \"micropip\", \"_pyodide\", \"_pyodide_core\"):\n            raise ImportError(f\"import of '{name}' is disabled in the DataPilot sandbox\")\n        return None\n\n\nfor _name in list(sys.modules):\n    if _name in _BLOCKED or _name.split(\".\")[0] in (\"js\", \"pyodide_js\", \"micropip\"):\n        sys.modules.pop(_name, None)\nsys.meta_path.insert(0, _BlockImports())\n\nimport builtins as _b  # noqa: E402\n\n_real_import = _b.__import__\n\n\ndef _guarded_import(name, *args, **kwargs):\n    if name in _BLOCKED or name.split(\".\")[0] in (\"js\", \"pyodide_js\", \"pyodide\", \"micropip\", \"_pyodide\", \"_pyodide_core\"):\n        raise ImportError(f\"import of '{name}' is disabled in the DataPilot sandbox\")\n    return _real_import(name, *args, **kwargs)\n\n\n_b.__import__ = _guarded_import\n# exec/compile stay: the import system needs them (the server-side precheck rejects them in model code).\nfor _fn in (\"open\", \"input\", \"breakpoint\"):\n    if hasattr(_b, _fn):\n        def _deny(*_a, _n=_fn, **_k):\n            raise PermissionError(f\"{_n}() is disabled in the DataPilot sandbox\")\n        setattr(_b, _fn, _deny)\ndel _fn\n";
const PRELUDE_PY = "# Builds `data` from the JSON payload, then user code runs, then `result` is serialized.\nimport json as _json\n\nimport numpy as np  # noqa: F401\nimport pandas as pd\n\n_payload = _json.loads(_DP_PAYLOAD)  # noqa: F821 — injected by the runner\ndata = pd.DataFrame(_payload[\"rows\"], columns=_payload[\"columns\"])\ndel _payload\n";
const EPILOGUE_PY = "import json as _json\nimport math as _math\n\n\ndef _clean(o, depth=0):\n    if depth > 6:\n        return str(o)\n    try:\n        import numpy as _np\n        import pandas as _pd\n        if isinstance(o, _np.generic):\n            o = o.item()\n        elif isinstance(o, _np.ndarray):\n            o = o.tolist()\n        elif isinstance(o, _pd.DataFrame):\n            o = o.head(50).to_dict(orient=\"records\")\n        elif isinstance(o, _pd.Series):\n            o = o.head(50).to_dict()\n    except Exception:\n        pass\n    if isinstance(o, float):\n        return None if (_math.isnan(o) or _math.isinf(o)) else round(o, 6)\n    if isinstance(o, dict):\n        return {str(k): _clean(v, depth + 1) for k, v in list(o.items())[:100]}\n    if isinstance(o, (list, tuple)):\n        return [_clean(v, depth + 1) for v in list(o)[:200]]\n    if o is None or isinstance(o, (str, int, bool)):\n        return o\n    return str(o)\n\n\nif \"result\" not in globals():\n    raise NameError(\"The code must assign the final answer to a variable named `result`.\")\n_DP_OUT = _json.dumps(_clean(result))  # noqa: F821\nif len(_DP_OUT) > 50_000:\n    raise ValueError(\"`result` is larger than 50 KB; return a summary instead.\")\n_DP_OUT\n";
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
