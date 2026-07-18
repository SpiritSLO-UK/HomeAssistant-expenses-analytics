import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Self-cleaning Vendors merge flow. It creates two uniquely-named vendors,
// folds the first into the second via the "Merge vendors" card, verifies the
// absorbed one is gone and the survivor remains, then deletes the survivor so
// the demo database ends the run exactly as it started.

// Unique per run so parallel/current data never collides with leftovers.
const RUN = `E2E-${Date.now().toString(36)}`;

// Accept the in-app confirm dialog (FE-10) by its action button.
async function confirmDialog(page: Page, label: RegExp | string = /delete/i) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

// Create a vendor via the "Add a vendor" form and confirm its row is present.
async function addVendor(page: Page, canonicalName: string) {
  await page.getByLabel("Canonical vendor name").fill(canonicalName);
  await page.getByRole("button", { name: "Add vendor" }).click();
  await expect(page.locator("tbody tr").filter({ hasText: canonicalName })).toHaveCount(1);
}

test.describe("vendors merge (self-cleaning)", () => {
  test("create two vendors, merge one into the other, clean up", async ({ page }) => {
    const absorbed = `${RUN}-vendor-a`;
    const survivor = `${RUN}-vendor-b`;
    await gotoPage(page, { route: "/vendors", heading: "Vendors" });

    // 1. Create both vendors and assert each has exactly one table row.
    await addVendor(page, absorbed);
    await addVendor(page, survivor);

    // 2. Merge the first (absorbed and deleted) into the second (kept).
    await page
      .getByLabel("Vendor to merge (absorbed and deleted)")
      .selectOption({ label: absorbed });
    await page
      .getByLabel("Vendor to keep (absorbs the first)")
      .selectOption({ label: survivor });
    await page.getByRole("button", { name: /^Merge$/ }).click();
    await confirmDialog(page, "Merge");

    // 3. The absorbed vendor is gone; the survivor remains.
    await expect(page.locator("tbody tr").filter({ hasText: absorbed })).toHaveCount(0);
    const survivorRow = page.locator("tbody tr").filter({ hasText: survivor });
    await expect(survivorRow).toHaveCount(1);

    // 4. Cleanup: delete the survivor and verify it is gone.
    await survivorRow.getByRole("button", { name: "Delete" }).click();
    await confirmDialog(page);
    await expect(page.locator("tbody tr").filter({ hasText: survivor })).toHaveCount(0);
  });
});
