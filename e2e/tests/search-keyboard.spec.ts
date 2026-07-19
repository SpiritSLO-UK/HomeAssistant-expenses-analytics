import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Global search: keyboard navigation" (#13). The highlight/hint
// stays hidden until an arrow key is pressed; arrows move a visible highlight and
// Enter opens it; category/vendor chips deep-link into filtered Transactions.
// Read-only (searches, navigates) - it mutates nothing.

const HINT = /Use ↑ and ↓ to navigate, Enter to open\./;

test.describe("global search: keyboard navigation and chips (#13)", () => {
  test("arrow keys reveal + move a highlight; Enter opens the result", async ({ page, request }) => {
    // Search for a substring of a real category name so results are guaranteed.
    const cats = (await (await request.get("/api/categories")).json()) as Array<{ name: string }>;
    test.skip(cats.length === 0, "demo has no categories to search");
    const term = cats[0].name.slice(0, 3);

    await gotoPage(page, { route: "/search", heading: "Search" });
    const input = page.locator("#search-input");
    await input.fill(term);
    await expect(page.locator("#search-results")).toBeVisible();

    // Mouse users: no keyboard hint / highlight before any arrow key.
    await expect(page.getByText(HINT)).toHaveCount(0);

    // Arrow down reveals the hint and sets the active descendant.
    await input.press("ArrowDown");
    await expect(page.getByText(HINT)).toBeVisible();
    const firstActive = await input.getAttribute("aria-activedescendant");
    expect(firstActive, "an item is highlighted after ArrowDown").toBeTruthy();

    // Arrow down again moves the highlight to a different item.
    await input.press("ArrowDown");
    await expect
      .poll(() => input.getAttribute("aria-activedescendant"))
      .not.toBe(firstActive);

    // Enter opens the highlighted result; every result deep-links into Transactions.
    await input.press("Enter");
    await expect(page.getByRole("heading", { name: "Transactions" }).first()).toBeVisible();
  });

  test("a category chip navigates to Transactions filtered by that entity", async ({ page, request }) => {
    const cats = (await (await request.get("/api/categories")).json()) as Array<{ name: string }>;
    test.skip(cats.length === 0, "demo has no categories to search");
    const term = cats[0].name.slice(0, 3);

    await gotoPage(page, { route: "/search", heading: "Search" });
    await page.locator("#search-input").fill(term);
    await expect(page.locator("#search-results")).toBeVisible();

    const chip = page.locator('[id^="sr-cat-"]').first();
    await expect(chip).toBeVisible();
    await chip.click();

    await expect(page.getByRole("heading", { name: "Transactions" }).first()).toBeVisible();
    expect(page.url()).toMatch(/category_id=/);
  });
});
