import { NextResponse } from "next/server";
import { auth, appUserFrom } from "@/auth";
import { mintBackendToken } from "@/lib/server/backend-token";
import { serverEnv } from "@/lib/server/env";

/**
 * Hands the browser a 5-minute token that can only call POST /v1/datasets, so files go straight to the API
 * (Vercel functions accept at most 4.5 MB request bodies; uploads allow 10 MB per file).
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const user = appUserFrom(await auth());
  if (!user) return NextResponse.json({ error: { code: "unauthorized", message: "Sign in (or try as a guest) to upload data." } }, { status: 401 });
  const base = serverEnv.backendUrl();
  if (!base) return NextResponse.json({ error: { code: "backend_unconfigured", message: "The API server isn't connected yet." } }, { status: 503 });
  const token = await mintBackendToken({ sub: user.sub, role: user.role, name: user.name, login: user.login, scope: "upload" });
  return NextResponse.json({ url: `${base}/v1/datasets`, token }, { headers: { "Cache-Control": "no-store" } });
}
