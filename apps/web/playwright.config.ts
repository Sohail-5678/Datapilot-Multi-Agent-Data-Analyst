import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against the real local stack (SPEC §13.3): FastAPI with FAKE_LLM=true + the production web build.
 * Keys: E2E_JWT_PRIVATE_KEY (base64 PKCS8 PEM) / E2E_JWT_PUBLIC_KEY (base64 SPKI PEM) — CI generates a throwaway pair.
 */
const API_PORT = 10088;
const WEB_PORT = 3188;

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `uv run uvicorn datapilot.api.main:app --port ${API_PORT}`,
      cwd: "../../backend",
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        FAKE_LLM: "true",
        GROQ_API_KEY: "",
        GEMINI_API_KEY: "",
        DATABASE_URL: "sqlite:///./.devdata/e2e.db",
        JWT_PUBLIC_KEY: process.env.E2E_JWT_PUBLIC_KEY ?? "",
        RATE_LIMITS_JSON: '{"guest": [1000, 1000], "user": [1000, 1000], "admin": [1000, 1000]}',
        ALLOWED_ORIGINS: `http://localhost:${WEB_PORT}`,
        UPLOAD_CACHE_DIR: "./.devdata/e2e-uploads",
      },
    },
    {
      command: `pnpm build && pnpm start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 300_000,
      env: {
        BACKEND_URL: `http://127.0.0.1:${API_PORT}`,
        JWT_PRIVATE_KEY: process.env.E2E_JWT_PRIVATE_KEY ?? "",
        AUTH_SECRET: "e2e-only-secret-not-used-anywhere-else",
        AUTH_URL: `http://localhost:${WEB_PORT}`,
        AUTH_TRUST_HOST: "true",
      },
    },
  ],
});
