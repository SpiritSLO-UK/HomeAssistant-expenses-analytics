import { test, expect } from "@playwright/test";

// Deep-linkable Settings sub-tab sections (?section=). The Settings page reads the
// active section from the URL query param and syncs it back on tab switches, so a
// cross-link (e.g. from the Dashboard) can land directly on the right section
// rather than always on General. Security is chosen here because its tab is always
// visible (not owner/manager-gated).

test("settings deep-link: ?section=security opens on the Security tab", async ({ page }) => {
  await page.goto("/#/settings?section=security");
  await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();

  const tablist = page.getByRole("tablist", { name: "Settings sections" });
  await expect(tablist.getByRole("tab", { name: /Security/ })).toHaveAttribute("aria-selected", "true");
  await expect(tablist.getByRole("tab", { name: /General/ })).toHaveAttribute("aria-selected", "false");
});

test("settings deep-link: switching a sub-tab syncs the URL", async ({ page }) => {
  await page.goto("/#/settings");
  const tablist = page.getByRole("tablist", { name: "Settings sections" });
  await expect(tablist.getByRole("tab", { name: /General/ })).toHaveAttribute("aria-selected", "true");

  await tablist.getByRole("tab", { name: /Data/ }).click();
  await expect(tablist.getByRole("tab", { name: /Data/ })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/section=data/);
});

test("settings deep-link: an unknown section falls back to General", async ({ page }) => {
  await page.goto("/#/settings?section=bogus");
  const tablist = page.getByRole("tablist", { name: "Settings sections" });
  await expect(tablist.getByRole("tab", { name: /General/ })).toHaveAttribute("aria-selected", "true");
});
