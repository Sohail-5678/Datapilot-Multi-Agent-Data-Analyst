import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";
const PYODIDE_CDN = "https://cdn.jsdelivr.net";
// "Your data" uploads go straight from the browser to the API (Vercel caps function bodies at 4.5 MB).
const API_ORIGIN = (() => {
  try {
    return process.env.BACKEND_URL ? new URL(process.env.BACKEND_URL).origin : "";
  } catch {
    return "";
  }
})();

// Next.js injects small inline bootstrap scripts, so script-src needs 'unsafe-inline' (nonces would make every
// page dynamic). 'wasm-unsafe-eval' lets the Pyodide worker compile WebAssembly; the CDN is pinned to jsDelivr.
// Vega runs with its CSP-safe expression interpreter, so no 'unsafe-eval' in production.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' ${PYODIDE_CDN}${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data: blob: https://avatars.githubusercontent.com",
  `connect-src 'self' ${PYODIDE_CDN}${API_ORIGIN ? ` ${API_ORIGIN}` : ""}${isDev ? " ws: wss:" : ""}`,
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
  "form-action 'self' https://github.com",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
