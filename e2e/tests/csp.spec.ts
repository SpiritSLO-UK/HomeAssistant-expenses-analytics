import { test, expect } from "@playwright/test";
import { PAGES, gotoPage, type PageDef } from "./helpers";

// Release-check A4 (#326): prove the SPA renders under the backend-served
// Content-Security-Policy. The header can only be verified end-to-end against
// the running app, so CI relies on this spec rather than a unit test.

// Any console/pageerror text that looks like a browser CSP rejection. If the
// served policy blocked the SPA's own scripts, styles, fonts, or XHR, the
// browser logs one of these phrases.
const CSP_VIOLATION =
  /content security policy|refused to (load|execute|connect|apply)|violates the following/i;

// Representative slice of the app: dashboard, a data-heavy list, settings, and
// search. Enough surface to exercise the SPA's own asset loading under the CSP.
const SAMPLE_ROUTES = ["/", "/transactions", "/settings", "/search"];

function sampledPages(): PageDef[] {
  return SAMPLE_ROUTES.map((route) => {
    const def = PAGES.find((p) => p.route === route);
    if (!def) throw new Error(`No PAGES entry for route ${route}`);
    return def;
  });
}

test.describe("content-security-policy", () => {
  test("root document carries a non-empty CSP response header", async ({
    page,
  }) => {
    const response = await page.goto("/");
    expect(response, "no response for root document").not.toBeNull();

    const headers = response ? response.headers() : {};
    const csp = headers["content-security-policy"] ?? "";
    expect(csp.trim().length, "content-security-policy header missing or empty").toBeGreaterThan(0);
  });

  test("navigating the app produces no CSP violations", async ({ page }) => {
    const violations: string[] = [];
    const record = (text: string): void => {
      if (CSP_VIOLATION.test(text)) violations.push(text);
    };
    page.on("console", (msg) => record(msg.text()));
    page.on("pageerror", (err) => record(String(err)));

    for (const def of sampledPages()) {
      await gotoPage(page, def);
    }

    expect(violations, "browser reported CSP violations").toEqual([]);
  });
});
