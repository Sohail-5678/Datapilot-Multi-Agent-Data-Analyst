// Sandbox tests (SPEC §13.3): correct result, network blocked, escapes blocked, infinite loop killed, big data refused.
import assert from "node:assert/strict";
import { test } from "node:test";
import { runSandboxed } from "../runner.mjs";

const data = { columns: ["a", "b"], rows: [[1, 2], [2, 4.1], [3, 6.2], [4, 7.9]] };

test("computes a result from `data`", async () => {
  const r = await runSandboxed({ code: "r = data.a.corr(data.b)\nresult = {'pearson_r': round(float(r), 4), 'n': len(data)}\nprint('ok')", data });
  assert.equal(r.ok, true, r.error);
  assert.equal(r.result.n, 4);
  assert.ok(r.result.pearson_r > 0.99);
  assert.match(r.stdout, /ok/);
  assert.equal(r.ran_in, "node");
});

test("network calls from Python throw", async () => {
  const r = await runSandboxed({ code: "import pyodide.http\nresult = 1", data });
  assert.equal(r.ok, false);
  assert.match(r.error, /disabled/);
});

test("js module is blocked", async () => {
  const r = await runSandboxed({ code: "import js\nresult = str(js.fetch)", data });
  assert.equal(r.ok, false);
  assert.match(r.error, /disabled/);
});

test("__import__ bypass is blocked", async () => {
  const r = await runSandboxed({ code: "m = __import__('js')\nresult = 1", data });
  assert.equal(r.ok, false);
});

test("micropip is blocked", async () => {
  const r = await runSandboxed({ code: "import micropip\nresult = 1", data });
  assert.equal(r.ok, false);
});

test("open() is disabled", async () => {
  const r = await runSandboxed({ code: "open('/etc/passwd').read()\nresult = 1", data });
  assert.equal(r.ok, false);
  assert.match(r.error, /disabled/);
});

test("missing result is an error", async () => {
  const r = await runSandboxed({ code: "x = 1", data });
  assert.equal(r.ok, false);
  assert.match(r.error, /result/);
});

test("infinite loop is killed at the timeout", async () => {
  const t0 = Date.now();
  const r = await runSandboxed({ code: "while True:\n    pass", data }, 2000);
  assert.equal(r.ok, false);
  assert.match(r.error, /Timed out/);
  assert.ok(Date.now() - t0 < 60_000);
});

test("big data is refused", async () => {
  const rows = Array.from({ length: 5001 }, (_, i) => [i, i]);
  const r = await runSandboxed({ code: "result = 1", data: { columns: ["a", "b"], rows } });
  assert.equal(r.ok, false);
  assert.match(r.error, /too large/);
});
