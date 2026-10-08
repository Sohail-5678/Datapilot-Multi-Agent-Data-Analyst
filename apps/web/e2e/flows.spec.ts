import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function guest(page: Page, next = "/app") {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByRole("button", { name: "Try as guest" }).click();
  await page.waitForURL((u) => u.pathname.startsWith("/app"));
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).disableRules(["color-contrast"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
}

test("landing page renders and is accessible", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Ask in plain English");
  await axe(page);
});

test("ask → answer → chart → SQL tab", async ({ page }) => {
  await guest(page);
  await page.getByLabel("Ask a question about this database").fill("Which 10 artists have the most tracks?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Numbers checked against results")).toBeVisible();
  await expect(page.getByRole("tab", { name: /Chart/ })).toBeVisible();
  await page.getByRole("tab", { name: /Table/ }).click();
  await expect(page.getByRole("table", { name: "Query results" })).toContainText("Iron Maiden");
  await page.getByRole("tab", { name: "SQL" }).click();
  await expect(page.getByText(/^SQL · \d of \d candidates agree/)).toBeVisible();
  await axe(page);
});

test("clarify → confirm → answer (human checkpoints)", async ({ page }) => {
  await guest(page, "/app?db=european_football_2");
  await page.getByLabel("Ask a question about this database").fill("Show me the best players");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByRole("button", { name: "Highest overall rating" }).click();
  await expect(page.getByText("Run this query?")).toBeVisible();
  await expect(page.getByRole("alertdialog")).toContainText("183,978");
  await page.getByRole("button", { name: "Run anyway" }).click();
  await expect(page.getByText("Numbers checked against results")).toBeVisible();
});

test("cancel at the confirmation stops the run", async ({ page }) => {
  await guest(page, "/app?db=european_football_2");
  await page.getByLabel("Ask a question about this database").fill("Average overall rating by preferred foot");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText(/Cancelled/)).toBeVisible();
});

test("analysis runs in the browser sandbox", async ({ page }) => {
  test.slow(); // first Pyodide load downloads ~10 MB from the CDN
  await guest(page);
  await page.getByLabel("Ask a question about this database").fill("Is there a correlation between track length and price?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText(/Ran in your browser · no network/)).toBeVisible({ timeout: 120_000 });
  await page.getByRole("tab", { name: "Python" }).click();
  await expect(page.getByText("pearson_r").first()).toBeVisible();
});

test("benchmarks and databases pages are accessible", async ({ page }) => {
  await page.goto("/benchmarks");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await axe(page);
  await page.goto("/databases/chinook");
  await expect(page.getByRole("heading", { name: "Chinook", level: 1 })).toBeVisible();
  await axe(page);
});

test("upload your own data → ask about it → delete", async ({ page }) => {
  await guest(page, "/app");
  await page.getByRole("button", { name: "Upload your data" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles("e2e/fixtures/sales.csv");
  await expect(dialog.getByRole("button", { name: "Upload & analyze" })).toBeDisabled(); // consent required
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Upload & analyze" }).click();
  await expect(dialog.getByText("Ready")).toBeVisible();
  await expect(dialog).toContainText("amount");
  await axe(page);
  await dialog.getByRole("button", { name: "Start asking" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("sales");
  await page.getByLabel("Ask a question about this database").fill("How many orders are there?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("Numbers checked against results")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete" }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Chinook");
});
