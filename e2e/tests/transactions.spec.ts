import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

test("transactions: filters, CSV export and rows render", async ({ page }) => {
  await gotoPage(page, { route: "/transactions", heading: "Transactions" });
  await expect(page.getByRole("button", { name: /Export CSV/i })).toBeVisible();
  // Range presets from the header toolbar.
  await expect(page.getByRole("button", { name: /^This month$/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /^This year$/i })).toBeVisible();
  // The demo has transactions: at least one per-row category <select> is present.
  await expect(page.locator("select").first()).toBeVisible();
});

test("transactions: switching a range preset keeps the page healthy", async ({ page }) => {
  await gotoPage(page, { route: "/transactions", heading: "Transactions" });
  await page.getByRole("button", { name: /^This year$/i }).click();
  // No crash; the heading and export button survive the refetch.
  await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Export CSV/i })).toBeVisible();
});
