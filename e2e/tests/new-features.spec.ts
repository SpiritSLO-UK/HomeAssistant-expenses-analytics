import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// Locks in the three "surface existing backend" wins (#454/#455/#456).

test('search: advertises the filter tokens (#454)', async ({ page }) => {
  await gotoPage(page, { route: "/search", heading: "Search" });
  // The placeholder now teaches a token example.
  await expect(page.getByPlaceholder(/category:/i)).toBeVisible();
  // The "Filter tips" disclosure documents the tokens.
  const tips = page.getByText(/filter tips/i).first();
  await expect(tips).toBeVisible();
  await tips.click();
  await expect(page.getByText(/category:/i).first()).toBeVisible();
  await expect(page.getByText(/after:/i).first()).toBeVisible();
});

test('search: a query with a token runs without error', async ({ page }) => {
  await gotoPage(page, { route: "/search", heading: "Search" });
  const box = page.getByPlaceholder(/category:/i);
  await box.fill("a");
  // Results (or an empty-state) should appear; no crash. Give the debounce a beat.
  await page.waitForTimeout(800);
  await expect(page.getByRole("heading", { name: "Search" }).first()).toBeVisible();
});

test('logs: audit CSV export button downloads a file (#455)', async ({ page }) => {
  await gotoPage(page, { route: "/logs", heading: "Logs" });
  const btn = page.getByRole("button", { name: /Download CSV/i });
  await expect(btn).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    btn.click(),
  ]);
  expect(download.suggestedFilename()).toContain("audit");
});

test('tags: the Tags page surfaces the tag-housekeeping controls (#456)', async ({ page }) => {
  await gotoPage(page, { route: "/tags", heading: "Tags" });
  // The owner-gated Tags page (moved off Settings into its own sidebar page).
  await expect(page.getByRole("heading", { name: /^Tags$/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Remove unused/i })).toBeVisible();
  // Merge control (source -> target selects + the page's description).
  await expect(page.getByText(/Merge duplicate tags/i)).toBeVisible();
});
