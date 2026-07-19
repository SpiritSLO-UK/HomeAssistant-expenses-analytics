import { test, expect } from "@playwright/test";

// Grouped sidebar + page sub-tabs (grouped-nav PR2). The default layout groups
// Import / Transactions / Receipts / Review under a "Money" group: the sidebar
// shows the group header (not the individual pages), and the members appear as a
// sub-tab strip above the page. Standalone pages (e.g. Search) show no sub-tabs.

test("grouped nav: a group renders as a header and its members become sub-tabs", async ({ page }) => {
  // Land on a member of the multi-item "Money" group.
  await page.goto("/#/import");
  await expect(page.getByRole("heading", { name: "Import" }).first()).toBeVisible();

  // The sidebar shows the GROUP header, not a flat per-page link.
  const moneyHeader = page.locator("aside").getByRole("link", { name: /Money/ });
  await expect(moneyHeader).toBeVisible();

  // The page shows a sub-tab strip listing the group's members.
  const tablist = page.getByRole("tablist");
  await expect(tablist).toBeVisible();
  await expect(tablist.getByRole("tab", { name: /Transactions/ })).toBeVisible();
  await expect(tablist.getByRole("tab", { name: /Receipts/ })).toBeVisible();

  // Switching members via a sub-tab navigates without leaving the group.
  await tablist.getByRole("tab", { name: /Transactions/ }).click();
  await expect(page.getByRole("heading", { name: "Transactions" }).first()).toBeVisible();
  await expect(page.getByRole("tablist")).toBeVisible();

  // The group header links to its first visible member (Import).
  await moneyHeader.click();
  await expect(page.getByRole("heading", { name: "Import" }).first()).toBeVisible();
});

test("grouped nav: a standalone page shows no sub-tabs", async ({ page }) => {
  await page.goto("/#/search");
  await expect(page.getByRole("heading", { name: "Search" }).first()).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(0);
});
