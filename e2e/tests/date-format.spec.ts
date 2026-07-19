import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Settings: app-wide date format" (#17). Switching the format
// updates displayed dates across pages and the choice persists. Self-restoring:
// captures the original format and puts it back at the end.

test("date-format switch updates dates app-wide and persists (#17)", async ({ page }) => {
  await gotoPage(page, { route: "/settings", heading: "Settings" });

  const select = page.getByLabel("Date format");
  await expect(select).toBeVisible();
  const original = await select.inputValue();
  // Pick a target that is visibly distinct from ISO so a date on another page
  // proves the change: ISO is yyyy-mm-dd, US/UK are slash-separated.
  const target = original === "iso" ? "us" : "iso";
  const dateRe = target === "iso" ? /\d{4}-\d{2}-\d{2}/ : /\d{1,2}\/\d{1,2}\/\d{4}/;

  try {
    await select.selectOption(target);
    await expect(select).toHaveValue(target); // reflects immediately

    // Persists across a reload.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();
    await expect(page.getByLabel("Date format")).toHaveValue(target);

    // Dates on another page now render in the chosen format.
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });
    await expect(page.getByText(dateRe).first()).toBeVisible();
  } finally {
    // Restore the original preference so the demo ends as it started.
    await gotoPage(page, { route: "/settings", heading: "Settings" });
    await page.getByLabel("Date format").selectOption(original);
    await expect(page.getByLabel("Date format")).toHaveValue(original);
  }
});
