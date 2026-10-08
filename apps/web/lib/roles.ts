export type Role = "guest" | "user" | "admin";
export const ROLES: Role[] = ["guest", "user", "admin"];
export const isRole = (v: unknown): v is Role => typeof v === "string" && (ROLES as string[]).includes(v);

/** Pages that need a session (the proxy redirects to /login). Everything else is public. */
export function isProtectedPage(pathname: string) {
  return pathname === "/app" || pathname.startsWith("/app/") || pathname.startsWith("/runs/");
}

/**
 * Which backend routes an anonymous visitor may call (read-only catalog browsing).
 * Everything else needs a guest or GitHub session.
 */
export function anonymousAllowed(method: string, segments: string[]) {
  return method === "GET" && (segments[0] === "databases" || segments[0] === "benchmarks");
}
