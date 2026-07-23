import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// Settings "Delete all transactions" card (owner-only mass delete). Deliberately
// stops at the confirm and CANCELS - it never confirms a delete, so the shared
// demo data is never wiped. The actual deletion behaviour is covered by backend
// tests (owner-gating, filter scope, safety backup).
test.describe("settings: delete all transactions", () => {
  test("owner sees the card and the danger confirm can be cancelled", async ({ page }) => {
    await gotoPage(page, { route: "/settings", heading: "Settings" });
    await page.getByRole("tablist").getByRole("tab", { name: /Data/ }).click();

    const card = page.locator(".card").filter({ hasText: "Delete all transactions" });
    await expect(card).toBeVisible();

    // Opening it raises a danger confirm; cancelling changes nothing.
    await card.getByRole("button", { name: "Delete all transactions" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/permanently delete all transactions/i)).toBeVisible();
    await dialog.getByRole("button", { name: /cancel/i }).click();
    await expect(dialog).toBeHidden();
  });
});
