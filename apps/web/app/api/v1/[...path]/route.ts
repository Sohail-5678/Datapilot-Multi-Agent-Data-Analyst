import { NextResponse, type NextRequest } from "next/server";
import { auth, appUserFrom } from "@/auth";
import { anonymousAllowed } from "@/lib/roles";
import { mintBackendToken, type BackendClaims } from "@/lib/server/backend-token";
import { serverEnv } from "@/lib/server/env";

/**
 * Authenticated proxy: /api/v1/* → ${BACKEND_URL}/v1/* (SPEC §3.2).
 * Reads the Auth.js session, mints a 5-minute ES256 JWT, and passes SSE streams through unbuffered.
 * Anonymous visitors may only read the database catalog.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const MAX_BODY_BYTES = 64 * 1024;
const HEADERS_TIMEOUT_MS = { default: 15_000, stream: 30_000 };

function apiError(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

const starting = () =>
  apiError(503, "backend_starting", "Waking up the free server — this takes up to a minute after it has been idle.");

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path = [] } = await ctx.params;
  const segments = path.map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  if (segments.some((s) => s === ".." || s.includes("/"))) return apiError(400, "bad_request", "Bad path.");

  const user = appUserFrom(await auth());
  const ip = (req.headers.get("x-forwarded-for")?.split(",")[0] ?? req.headers.get("x-real-ip") ?? "").trim().slice(0, 64) || undefined;
  let claims: BackendClaims;
  if (user) {
    claims = { sub: user.sub, role: user.role, name: user.name, login: user.login, ip };
  } else if (anonymousAllowed(req.method, segments)) {
    claims = { sub: "anon:public", role: "guest", name: "Visitor", ip };
  } else {
    return apiError(401, "unauthorized", "Please sign in (or try as a guest).");
  }

  let bodyText: string | undefined;
  if (req.method === "POST") {
    bodyText = await req.text();
    if (bodyText.length > MAX_BODY_BYTES) return apiError(413, "validation_error", "Request body too large.");
  }

  const base = serverEnv.backendUrl();
  if (!base) return apiError(503, "backend_unconfigured", "The API server isn't connected yet.");

  let token: string;
  try {
    token = await mintBackendToken(claims);
  } catch {
    return apiError(500, "internal", "The web server can't sign backend requests (JWT_PRIVATE_KEY).");
  }

  const target = `${base}/v1/${segments.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const isStream =
    (req.method === "POST" && segments[0] === "threads" && segments[2] === "ask") ||
    (req.method === "GET" && segments[0] === "runs" && segments[2] === "events");

  const headers = new Headers({ Authorization: `Bearer ${token}`, Accept: isStream ? "text/event-stream" : "application/json" });
  if (bodyText !== undefined) headers.set("Content-Type", "application/json");
  const lastEventId = req.headers.get("last-event-id");
  if (lastEventId) headers.set("Last-Event-ID", lastEventId.slice(0, 12));

  const upstreamCtl = new AbortController();
  const onClientAbort = () => upstreamCtl.abort();
  req.signal.addEventListener("abort", onClientAbort, { once: true });
  const timer = setTimeout(() => upstreamCtl.abort(), isStream ? HEADERS_TIMEOUT_MS.stream : HEADERS_TIMEOUT_MS.default);

  let upstream: Response;
  try {
    upstream = await fetch(target, { method: req.method, headers, body: bodyText, signal: upstreamCtl.signal, cache: "no-store", redirect: "manual" });
  } catch {
    clearTimeout(timer);
    req.signal.removeEventListener("abort", onClientAbort);
    if (req.signal.aborted) return new Response(null, { status: 499 });
    return starting();
  }
  clearTimeout(timer);

  const contentType = upstream.headers.get("content-type") ?? "";
  // Render answers 502/503/504 (often HTML) while the free instance spins up.
  if ([502, 503, 504].includes(upstream.status) && !contentType.includes("application/json")) {
    await upstream.body?.cancel().catch(() => {});
    return starting();
  }

  const out = new Headers();
  if (contentType) out.set("Content-Type", contentType);
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) out.set("Retry-After", retryAfter);
  if (contentType.includes("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("Connection", "keep-alive");
    out.set("X-Accel-Buffering", "no");
    return new Response(upstream.body, { status: upstream.status, headers: out });
  }
  req.signal.removeEventListener("abort", onClientAbort);
  out.set("Cache-Control", "no-store");
  return new Response(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers: out });
}

export { proxy as DELETE, proxy as GET, proxy as POST };
