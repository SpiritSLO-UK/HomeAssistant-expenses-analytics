import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Self-cleaning Categories merge flow: create two uniquely-named categories,
// merge the first into the second, verify the source is gone and the target
// remains, then delete the target - so the demo database ends each run exactly
// as it started and reruns are idempotent.

// Unique per run so parallel/current data never collides with leftovers.
const RUN = `E2E-${Date.now().toString(36)}`;

// Accept the in-app confirm dialog (FE-10) by its action button.
async function confirmDialog(page: Page, label: RegExp | string) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

test.describe("categories merge (self-cleaning)", () => {
  test("create two, merge source into target, then delete the target", async ({ page }) => {
    const catA = `${RUN}-cat-a`;
    const catB = `${RUN}-cat-b`;

    // Scope presence checks to the library chips so the merge selects' <option>
    // elements (which also carry the names) never satisfy a match.
    const chip = (name: string) => page.locator(".chip").filter({ hasText: name });

    await gotoPage(page, { route: "/categories", heading: "Categories" });

    // 1. Create both categories via the Add form.
    const addName = page.getByPlaceholder("New category name");
    const addBtn = page.getByRole("button", { name: "Add", exact: true });

    await addName.fill(catA);
    await addBtn.click();
    await expect(chip(catA)).toBeVisible();

    await addName.fill(catB);
    await addBtn.click();
    await expect(chip(catB)).toBeVisible();

    // 2. Merge catA and catB. Pick both (the second select filters out the chosen
    // first category), then choose which to keep. The "Keep" choice defaults to the
    // second ("into") category, so catB survives and catA is removed.
    await page.getByLabel("First category to merge").selectOption({ label: catA });
    await page.getByLabel("Second category to merge").selectOption({ label: catB });

    // The Keep control appears and defaults to keeping catB (the second category).
    await expect(page.getByRole("button", { name: catB, exact: true, pressed: true })).toBeVisible();

    await page.getByRole("button", { name: "Merge", exact: true }).click();
    await confirmDialog(page, "Merge");

    // Source is gone, target survives.
    await expect(chip(catA)).toHaveCount(0);
    await expect(chip(catB)).toBeVisible();

    // 3. Cleanup: delete the surviving target and confirm it is gone.
    await page.getByRole("button", { name: `Delete category ${catB}` }).click();
    await confirmDialog(page, "Delete");
    await expect(chip(catB)).toHaveCount(0);
  });
});
