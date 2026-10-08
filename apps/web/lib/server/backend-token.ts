import "server-only";
import { importPKCS8, SignJWT, type CryptoKey } from "jose";

/**
 * Mints the short-lived ES256 JWT the backend verifies (SPEC §10.4).
 * The browser never sees this token; the private key lives only in Vercel's env.
 */
export interface BackendClaims {
  sub: string;
  role: "guest" | "user" | "admin";
  name: string;
  login?: string | null;
  /** "upload": a token handed to the browser for one direct file upload to the API (scoped server-side). */
  scope?: "upload";
}

let cached: { raw: string; key: Promise<CryptoKey> } | null = null;

function signingKey(raw: string) {
  if (!raw) throw new Error("JWT_PRIVATE_KEY is not set");
  if (cached?.raw !== raw) {
    const pem = raw.startsWith("-----BEGIN") ? raw : Buffer.from(raw, "base64").toString("utf8");
    cached = { raw, key: importPKCS8(pem, "ES256") };
  }
  return cached.key;
}

export async function mintBackendToken(
  claims: BackendClaims,
  privateKey: string = process.env.JWT_PRIVATE_KEY ?? "",
): Promise<string> {
  const key = await signingKey(privateKey);
  const { sub, login, scope, ...rest } = claims;
  return new SignJWT({ ...rest, ...(login ? { login } : {}), ...(scope ? { scope } : {}) })
    .setProtectedHeader({ alg: "ES256", typ: "JWT" })
    .setIssuer("datapilot-web")
    .setAudience("datapilot-api")
    .setSubject(sub)
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(key);
}
