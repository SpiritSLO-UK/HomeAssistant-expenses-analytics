import { test, expect } from "@playwright/test";
import { PAGES, gotoPage, collectErrors } from "./helpers";

// Broad coverage: every top-level page renders its heading with no uncaught JS
// error. This is the backbone of the suite - if a page white-screens or throws,
// this catches it. Server 5xx during load are recorded and asserted too (with
// workers=1 the connection pool is not exhausted, so a 5xx here is a real bug).
test.describe("smoke: every page renders", () => {
  for (const def of PAGES) {
    test(`${def.route} renders "${def.heading}"`, async ({ page }) => {
      const { jsErrors, serverErrors } = collectErrors(page);
      await gotoPage(page, def);
      // Sidebar is present on every page (global shell rendered).
      await expect(page.locator("nav, aside").first()).toBeVisible();
      expect(jsErrors, `JS errors on ${def.route}`).toEqual([]);
      expect(serverErrors, `server 5xx on ${def.route}`).toEqual([]);
    });
  }
});
