// Captures README screenshots from a running local stack: node scripts/screenshots.mjs http://localhost:3100
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://localhost:3100";
const out = new URL("../../../docs/screenshots/", import.meta.url).pathname;
const browser = await chromium.launch();

async function session(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5, colorScheme: theme });
  await ctx.addInitScript((t) => localStorage.setItem("dp-theme", t), theme);
  return ctx;
}
async function guest(page, next) {
  await page.goto(`${base}/login?next=${encodeURIComponent(next)}`);
  await page.getByRole("button", { name: "Try as guest" }).click();
  await page.waitForURL((u) => u.pathname.startsWith("/app"));
}
async function ask(page, q) {
  await page.getByLabel("Ask a question about this database").fill(q);
  await page.getByRole("button", { name: "Ask" }).click();
}

const dark = await session("dark");
let p = await dark.newPage();
await p.goto(base);
await p.waitForTimeout(2500);
await p.screenshot({ path: `${out}landing.jpg`, quality: 82 });
await guest(p, "/app");
await ask(p, "Which 5 genres made the most revenue in 2012, and how did each change vs 2011?");
await p.getByText(/Numbers checked against results|wasn.t supported/).first().waitFor({ timeout: 120_000 });
await p.waitForTimeout(1500);
await p.screenshot({ path: `${out}workspace-answer.jpg`, quality: 82 });

const light = await session("light");
p = await light.newPage();
await guest(p, "/app?db=european_football_2");
await ask(p, "Show me the best players");
await p.getByText("DataPilot asks").waitFor({ timeout: 90_000 });
await p.screenshot({ path: `${out}clarify.jpg`, quality: 82 });
await p.getByRole("group", { name: "Clarifying question" }).getByRole("button").first().click();
await p.getByText(/Run this query\?|Numbers checked/).first().waitFor({ timeout: 120_000 });
await p.waitForTimeout(500);
await p.screenshot({ path: `${out}confirm.jpg`, quality: 82 });
await p.goto(`${base}/databases`);
await p.waitForTimeout(2000);
await p.screenshot({ path: `${out}databases.jpg`, quality: 82 });
await p.goto(`${base}/about`);
await p.waitForTimeout(1500);
await p.screenshot({ path: `${out}about.jpg`, quality: 82 });

const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: "dark", isMobile: true });
await phone.addInitScript(() => localStorage.setItem("dp-theme", "dark"));
p = await phone.newPage();
await p.goto(base);
await p.waitForTimeout(2000);
await p.screenshot({ path: `${out}landing-mobile.jpg`, quality: 80 });
await browser.close();
console.log("screenshots written to", out);
