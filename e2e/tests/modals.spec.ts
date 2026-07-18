import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// FE-10: native confirm/prompt/alert were replaced by an in-app modal system.
// Clicking a destructive action must open an accessible dialog; cancelling it
// leaves data untouched (this test is non-destructive - it always cancels).

test("delete opens the in-app confirm dialog and cancels cleanly (FE-10)", async ({ page }) => {
  await gotoPage(page, { route: "/budgets", heading: "Budgets" });

  const del = page.getByRole("button", { name: /^delete$/i });
  await expect(del.first()).toBeVisible(); // wait for the budget list to render
  const before = await del.count();

  await del.first().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: /cancel/i })).toBeVisible();

  await dialog.getByRole("button", { name: /cancel/i }).click();
  await expect(dialog).toBeHidden();

  // Nothing was deleted: the delete buttons are still all there.
  await expect(del).toHaveCount(before);
});

test("Escape closes the confirm dialog (FE-10 a11y)", async ({ page }) => {
  await gotoPage(page, { route: "/budgets", heading: "Budgets" });

  const del = page.getByRole("button", { name: /^delete$/i });
  await expect(del.first()).toBeVisible();

  await del.first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
