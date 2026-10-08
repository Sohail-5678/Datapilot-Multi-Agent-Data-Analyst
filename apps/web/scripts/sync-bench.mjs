// Copies the harness's bench/summary.json into the web app (Vercel builds only apps/web).
import { copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
copyFileSync(join(here, "..", "..", "..", "bench", "summary.json"), join(here, "..", "data", "bench-summary.json"));
console.log("data/bench-summary.json updated from bench/summary.json");
