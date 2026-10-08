import { NextResponse } from "next/server";
import { serverEnv } from "@/lib/server/env";

/** Unauthenticated passthrough to ${BACKEND_URL}/healthz for the "waking up" screen. */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const noStore = { "Cache-Control": "no-store" };

export async function GET() {
  const base = serverEnv.backendUrl();
  if (!base) return NextResponse.json({ status: "unconfigured" }, { status: 503, headers: noStore });
  try {
    const res = await fetch(`${base}/healthz`, { cache: "no-store", signal: AbortSignal.timeout(5_000) });
    if (!res.ok || !(res.headers.get("content-type") ?? "").includes("application/json")) {
      return NextResponse.json({ status: "starting" }, { status: 503, headers: noStore });
    }
    return NextResponse.json(await res.json(), { headers: noStore });
  } catch {
    return NextResponse.json({ status: "starting" }, { status: 503, headers: noStore });
  }
}
