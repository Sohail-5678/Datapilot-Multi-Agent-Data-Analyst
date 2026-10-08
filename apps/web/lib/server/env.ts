import "server-only";

/** Server-only environment accessors (read at request time, never bundled to the client). */
function list(v: string | undefined) {
  return (v ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export const serverEnv = {
  backendUrl: () => (process.env.BACKEND_URL ?? "").replace(/\/+$/, ""),
  githubEnabled: () => Boolean(process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET),
  adminGithubUsers: () => list(process.env.ADMIN_GITHUB_USERS),
  jwtPrivateKey: () => process.env.JWT_PRIVATE_KEY ?? "",
};
