"""Runtime configuration, read from environment variables (docs/SPEC.md §17.1).

Every model id is an env var (providers rename models often). Guard limits that the agent
profile must never change (SPEC §9.3) live here, not in the profile.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field(default="dev", alias="APP_ENV")
    git_sha: str = Field(default="dev", alias="RENDER_GIT_COMMIT")

    # App data store. Neon Postgres in production; a local SQLite file when unset (dev, CI, eval).
    database_url: str = Field(default="", alias="DATABASE_URL")
    dbs_dir: Path = Field(default=BACKEND_ROOT / "data" / "dbs", alias="DBS_DIR")
    profile_path: Path = Field(default=BACKEND_ROOT / "profiles" / "default.json", alias="PROFILE_PATH")

    # Web → API auth: 5-minute ES256 JWTs minted by the Next.js server (it holds the private key).
    jwt_public_key_b64: str = Field(default="", alias="JWT_PUBLIC_KEY")
    jwt_issuer: str = Field(default="datapilot-web", alias="JWT_ISSUER")
    jwt_audience: str = Field(default="datapilot-api", alias="JWT_AUDIENCE")
    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")

    # LLM providers (SPEC §9.2, §S.1)
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")  # AI Studio project "datapilot"
    main_model: str = Field(default="gemini-3-flash-preview", alias="MAIN_MODEL")
    lite_model: str = Field(default="gemini-3.1-flash-lite-preview", alias="LITE_MODEL")
    gemini_thinking_level: str = Field(default="low", alias="GEMINI_THINKING_LEVEL")
    embed_model: str = Field(default="gemini-embedding-001", alias="EMBED_MODEL")
    embed_dim: int = Field(default=768, alias="EMBED_DIM")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    fast_model: str = Field(default="qwen/qwen3.8-27b", alias="FAST_MODEL")
    alt_model: str = Field(default="openai/gpt-oss-120b", alias="ALT_MODEL")  # last-resort fallback; "" disables
    guard_model: str = Field(default="meta-llama/llama-prompt-guard-2-86m", alias="GUARD_MODEL")
    groq_reasoning_effort: str = Field(default="none", alias="GROQ_REASONING_EFFORT")
    fake_llm: bool = Field(default=False, alias="FAKE_LLM")
    llm_timeout_s: float = Field(default=25.0, alias="LLM_TIMEOUT_S")

    # Daily budgets per model kind. DataPilot's share of the shared Groq qwen budget is 50% (§S.1).
    daily_budget_main_requests: int = Field(default=900, alias="DAILY_BUDGET_MAIN_REQUESTS")
    daily_budget_main_tokens: int = Field(default=3_000_000, alias="DAILY_BUDGET_MAIN_TOKENS")
    daily_budget_lite_requests: int = Field(default=900, alias="DAILY_BUDGET_LITE_REQUESTS")
    daily_budget_lite_tokens: int = Field(default=2_000_000, alias="DAILY_BUDGET_LITE_TOKENS")
    daily_budget_fast_requests: int = Field(default=500, alias="DAILY_BUDGET_FAST_REQUESTS")
    daily_budget_fast_tokens: int = Field(default=100_000, alias="DAILY_BUDGET_FAST_TOKENS")
    daily_budget_alt_requests: int = Field(default=100, alias="DAILY_BUDGET_ALT_REQUESTS")  # 10% headroom share
    daily_budget_alt_tokens: int = Field(default=20_000, alias="DAILY_BUDGET_ALT_TOKENS")
    daily_budget_guard_requests: int = Field(default=5000, alias="DAILY_BUDGET_GUARD_REQUESTS")
    daily_budget_embed_requests: int = Field(default=900, alias="DAILY_BUDGET_EMBED_REQUESTS")

    # Locked limits (SPEC §9.3) — never part of the agent profile
    scan_confirm_rows: int = Field(default=100_000, alias="SCAN_CONFIRM_ROWS")
    max_k: int = Field(default=3, alias="MAX_K")
    max_repairs: int = Field(default=2, alias="MAX_REPAIRS")
    question_call_budget: int = Field(default=25, alias="QUESTION_CALL_BUDGET")
    question_token_budget: int = Field(default=60_000, alias="QUESTION_TOKEN_BUDGET")
    question_wall_s: float = Field(default=90.0, alias="QUESTION_WALL_S")
    max_plan_steps: int = 4
    row_cap: int = 1000
    sql_timeout_s: float = Field(default=5.0, alias="SQL_TIMEOUT_S")
    sandbox_wait_s: float = Field(default=60.0, alias="SANDBOX_WAIT_S")
    human_wait_s: float = Field(default=900.0, alias="HUMAN_WAIT_S")
    sandbox_max_rows: int = 5000
    guard_threshold: float = Field(default=0.8, alias="GUARD_THRESHOLD")
    max_question_chars: int = 1000

    # Rate limits per role (SPEC §10.4)
    rate_limits_json: str = Field(
        default='{"guest": [10, 30], "user": [30, 100], "admin": [1000, 10000]}', alias="RATE_LIMITS_JSON"
    )
    rate_ip_per_min: int = Field(default=60, alias="RATE_IP_PER_MIN")
    retention_days: int = Field(default=30, alias="RETENTION_DAYS")

    # AgentForge integration (SPEC §18)
    agentforge_url: str = Field(default="", alias="AGENTFORGE_URL")
    agentforge_key: str = Field(default="", alias="AGENTFORGE_KEY")
    profile_source: str = Field(default="local", alias="PROFILE_SOURCE")  # local | agentforge
    eval_mode: bool = Field(default=False, alias="EVAL_MODE")
    price_table_json: str = Field(default="", alias="PRICE_TABLE_JSON")

    @property
    def jwt_public_key(self) -> str:
        raw = self.jwt_public_key_b64.strip()
        if not raw:
            return ""
        return raw if raw.startswith("-----BEGIN") else base64.b64decode(raw).decode()

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if not url:
            path = BACKEND_ROOT / ".devdata" / "app.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def rate_limits(self) -> dict[str, tuple[int, int]]:
        return {k: (int(v[0]), int(v[1])) for k, v in json.loads(self.rate_limits_json).items()}

    @property
    def gemini_ready(self) -> bool:
        return bool(self.gemini_api_key) and not self.fake_llm

    @property
    def groq_ready(self) -> bool:
        return bool(self.groq_api_key) and not self.fake_llm

    @property
    def llm_available(self) -> bool:
        return self.fake_llm or self.gemini_ready or self.groq_ready


@lru_cache
def get_settings() -> Settings:
    return Settings()
