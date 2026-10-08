# DataPilot — notes for Claude Code

The full build spec is `docs/SPEC.md` (source of truth). The README lists the deliberate deviations.

## Layout
- `backend/` — FastAPI + LangGraph (Python 3.12, uv). `datapilot/agents/*` are the graph nodes, `sql/*` the guard/executor/explain/compare, `index/*` catalog + value/schema indexes + verified cache, `guards/*` input/grounding/PII, `llm/*` providers/router/quota/prices/fake, `runner.py` drives the graph through interrupts, `api/*` routes + run manager (SSE).
- `apps/web/` — Next.js 16 (App Router), Auth.js v5, Tailwind 4, Vélora design system in `app/globals.css`. `app/api/v1/[...path]` is the JWT-minting proxy. `sandbox/` + `public/sandbox/pyodide-worker.js` (generated) are the browser Pyodide sandbox.
- `sandbox/` — shared Python lockdown + the Node Pyodide runner used in eval/CI mode. Edit `sandbox/*.py`, then `pnpm --dir apps/web sync:sandbox`.
- `bench/`, `evals/` — BIRD Mini-Dev subset/splits/results and case.v1 eval cases.

## Commands
- Backend tests: `cd backend && uv run pytest -q` (FAKE_LLM; never calls providers). Lint: `uv run ruff check datapilot scripts && uv run ruff format --check datapilot scripts`.
- One question from the CLI: `cd backend && uv run python scripts/ask.py chinook "…"` (add `FAKE_LLM=true` to stay offline).
- Web: `cd apps/web && pnpm lint && pnpm typecheck && pnpm test && pnpm e2e`.
- Sandbox: `cd sandbox && npm test`.
- Deploy web: `cd apps/web && vercel deploy --prod` (project `datapilot-analyst`, CLI-deployed, not git-connected). API: Render Blueprint `render.yaml`.

## Rules
- $0 only: free tiers, no card, no keep-alive pings. Secrets live only in Vercel/Render/GitHub secrets and gitignored `backend/.env`.
- Guards, read-only enforcement, SCAN_CONFIRM_ROWS, sandbox limits, grounding, budgets and PII policy are locked — never move them into the agent profile.
- Never fabricate benchmark numbers; `bench/summary.json` is written only by the harness.
