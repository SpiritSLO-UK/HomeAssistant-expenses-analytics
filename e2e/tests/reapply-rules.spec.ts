import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// The "Re-apply categorisation rules" panel: dry-run preview + the scope /
// replace-existing toggles. Preview-only on purpose - it never clicks Apply, so
// it can't mutate the shared demo categories that other specs assert on.
test.describe("transactions: re-apply rules panel", () => {
  // Any concrete outcome of the dry-run is acceptable (it depends on the demo's
  // rows/rules): a positive "N of M", the "nothing uncategorised" nudge (default,
  // when the set is already categorised), or "no rule matches". What matters is
  // that the preview RESOLVES rather than staying on "Checking...".
  const previewResolved =
    /re-categorise \d+ of \d+|No transactions would change|Nothing here is uncategorised|no rule matches/i;

  test("opens the panel and shows a dry-run preview that reacts to the options", async ({ page }) => {
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });

    await page.getByRole("button", { name: /Re-apply rules/ }).click();

    const panel = page.locator(".card").filter({ hasText: "Re-apply categorisation rules" });
    await expect(panel).toBeVisible();
    await expect(panel.getByText(previewResolved)).toBeVisible();

    // Turning on "also replace existing auto-categories" re-previews for the wider
    // set and reveals the manual-override opt-in.
    await panel.getByRole("checkbox").first().check();
    await expect(panel.getByText(/also replace my manual/i)).toBeVisible();
    await expect(panel.getByText(previewResolved)).toBeVisible();

    // Close without applying - the demo data is left untouched.
    await panel.getByRole("button", { name: /Cancel/ }).click();
    await expect(panel).toBeHidden();
  });
});
