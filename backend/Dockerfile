# DataPilot API (SPEC §14.4): FastAPI + LangGraph + read-only SQLite demo databases, one container on Render free.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    MALLOC_ARENA_MAX=2 \
    PORT=10000

RUN useradd --create-home app
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY datapilot ./datapilot
COPY profiles ./profiles
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/fetch_dbs.py ./scripts/fetch_dbs.py
COPY data ./data
RUN uv sync --frozen --no-dev \
 && .venv/bin/python scripts/fetch_dbs.py \
 && chmod -R a-w data/dbs \
 && mkdir -p .devdata && chown -R app:app .devdata

ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 10000
# Migrations (no-op when current), then one uvicorn worker: run state, interrupts and the value index live in memory.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn datapilot.api.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
