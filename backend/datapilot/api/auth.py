"""Auth (SPEC §10.4): every /v1 route needs a 5-minute ES256 JWT minted by the Next.js server.

Deviation: ES256 (asymmetric) instead of HS256 — the web app holds the private key, the API only the public
key, so the public half can sit in render.yaml and a leaked API config can't mint tokens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header, Request
from sqlalchemy import select

from datapilot.api.errors import ApiError
from datapilot.config import get_settings
from datapilot.db.schema import app_users
from datapilot.db.session import run_db

ROLES = ("guest", "user", "admin")
_user_cache: dict[str, tuple[float, str]] = {}


@dataclass
class Principal:
    sub: str
    role: str
    name: str
    login: str | None
    user_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    if not s.jwt_public_key:
        raise ApiError(503, "backend_starting", "Authentication is not configured on the server yet.")
    try:
        return jwt.decode(
            token,
            s.jwt_public_key,
            algorithms=["ES256"],
            audience=s.jwt_audience,
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=10,
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(401, "unauthorized", "Your session token expired. Please retry.") from exc
    except jwt.PyJWTError as exc:
        raise ApiError(401, "unauthorized", "Invalid session token.") from exc


async def _user_id(p: Principal) -> str:
    hit = _user_cache.get(p.sub)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    provider, _, puid = p.sub.partition(":")

    def upsert(conn):  # type: ignore[no-untyped-def]
        row = conn.execute(
            select(app_users.c.id, app_users.c.role).where(
                (app_users.c.provider == provider) & (app_users.c.provider_user_id == puid)
            )
        ).first()
        if row:
            if row.role != p.role:
                conn.execute(app_users.update().where(app_users.c.id == row.id).values(role=p.role))
            return row.id
        res = conn.execute(
            app_users.insert().values(provider=provider, provider_user_id=puid, github_login=p.login, role=p.role)
        )
        return res.inserted_primary_key[0]

    uid = str(await run_db(upsert))
    if len(_user_cache) > 5000:
        _user_cache.clear()
    _user_cache[p.sub] = (time.monotonic() + 600, uid)
    return uid


async def current_principal(request: Request, authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Missing session token.")
    claims = decode_token(authorization.split(" ", 1)[1].strip())
    role = claims.get("role")
    if role not in ROLES:
        raise ApiError(403, "forbidden", "Unknown role.")
    p = Principal(sub=str(claims["sub"]), role=role, name=str(claims.get("name") or "Guest"), login=claims.get("login"))
    p.user_id = await _user_id(p)
    request.state.principal = p
    return p
