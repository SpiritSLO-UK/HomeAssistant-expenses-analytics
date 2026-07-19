import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Categories: accessibility, merge, bulk recolour" (#12). Merge is
// already covered by categories-merge.spec.ts; here we lock in the bulk recolour,
// the descriptive aria-labels, and the "new category defaults to an unused
// colour" behaviour. Self-cleaning: creates its own E2E categories and deletes
// them at the end.

const RUN = `E2EC-${Date.now().toString(36)}`;

async function confirmDialog(page: Page, label: RegExp | string) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

// "rgb(r, g, b)" for a #rrggbb hex, matching getComputedStyle output.
function hexToRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

test.describe("categories: bulk recolour + accessibility (self-cleaning)", () => {
  test("a new category defaults to a colour not already in use (#12)", async ({ page }) => {
    await gotoPage(page, { route: "/categories", heading: "Categories" });

    // The pre-filled colour for a new category.
    const colourInput = page.getByLabel("Colour for new category");
    const preset = (await colourInput.inputValue()).toLowerCase();
    expect(preset, "new-category colour is a valid hex").toMatch(/^#[0-9a-f]{6}$/);

    // It must not clash with any existing category swatch.
    const used = await page
      .locator(".chip__dot")
      .evaluateAll((els) => els.map((e) => getComputedStyle(e).backgroundColor));
    expect(used, "default colour must be unused").not.toContain(hexToRgb(preset));
  });

  test("bulk recolour applies to every ticked category; controls are labelled (#12)", async ({ page }) => {
    const catA = `${RUN}-a`;
    const catB = `${RUN}-b`;
    const chip = (name: string) => page.locator(".chip").filter({ hasText: name });

    await gotoPage(page, { route: "/categories", heading: "Categories" });

    // Create two categories.
    const addName = page.getByLabel("New category name");
    const addBtn = page.getByRole("button", { name: "Add", exact: true });
    await addName.fill(catA);
    await addBtn.click();
    await expect(chip(catA)).toBeVisible();
    await addName.fill(catB);
    await addBtn.click();
    await expect(chip(catB)).toBeVisible();

    // Accessibility: the per-category controls carry descriptive labels.
    await expect(page.getByRole("checkbox", { name: `Select ${catA} for bulk recolour` })).toBeVisible();
    await expect(page.getByRole("button", { name: `Delete category ${catA}` })).toBeVisible();

    // Tick both and apply a known colour.
    await page.getByRole("checkbox", { name: `Select ${catA} for bulk recolour` }).check();
    await page.getByRole("checkbox", { name: `Select ${catB} for bulk recolour` }).check();

    const colour = "#123456";
    await page.getByLabel("Colour to apply to selected categories").fill(colour);
    await page.getByRole("button", { name: "Apply colour to selected" }).click();

    // Both swatches now show the applied colour.
    const rgb = hexToRgb(colour);
    await expect(chip(catA).locator(".chip__dot")).toHaveCSS("background-color", rgb);
    await expect(chip(catB).locator(".chip__dot")).toHaveCSS("background-color", rgb);

    // Cleanup: delete both.
    for (const name of [catA, catB]) {
      await page.getByRole("button", { name: `Delete category ${name}` }).click();
      await confirmDialog(page, "Delete");
      await expect(chip(name)).toHaveCount(0);
    }
  });
});
