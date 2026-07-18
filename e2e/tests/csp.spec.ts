import { createHash } from "node:crypto";
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

// The CSP hash a browser computes for an inline <script>: the sha256, base64, of
// the element's text content with CRLF/CR newlines normalised to LF (per the HTML
// spec). We hash the FIRST attribute-less <script> — the pre-paint theme setter —
// which is the only inline script the app serves.
function inlineThemeScriptHash(servedHtml: string): string {
  const match = /<script>([\s\S]*?)<\/script>/.exec(servedHtml);
  if (!match) throw new Error("no inline <script> found in the served document");
  const body = match[1].replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const digest = createHash("sha256").update(body, "utf8").digest("base64");
  return `sha256-${digest}`;
}

// The `script-src` directive body (everything up to the next `;`), or "".
function scriptSrcOf(csp: string): string {
  const match = /script-src([^;]*)/.exec(csp);
  return match ? match[1] : "";
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

  // Regression guard for the inline-script hash/script DRIFT that a
  // "no console violation" check misses: fetch the ACTUAL served document and the
  // ACTUAL served CSP together, hash the inline theme script exactly as the browser
  // would, and assert that hash is whitelisted in script-src. We hash what is served
  // (Vite's built index.html), not the source file, so this also catches any
  // build-time transform or a stale hash left behind after editing the script.
  test("served CSP script-src whitelists the actual inline theme script", async ({
    request,
  }) => {
    const response = await request.get("/");
    expect(response.ok(), "root document did not load").toBeTruthy();

    const html = await response.text();
    const csp = response.headers()["content-security-policy"] ?? "";
    const scriptSrc = scriptSrcOf(csp);
    const hash = inlineThemeScriptHash(html);

    // The hash must be the thing that permits the script: if script-src fell back
    // to 'unsafe-inline' the hash would be moot and the drift guard meaningless.
    expect(scriptSrc, "script-src must not allow 'unsafe-inline'").not.toContain(
      "'unsafe-inline'",
    );
    expect(
      scriptSrc,
      `served script-src is missing the inline theme script hash ${hash} (hash/script drift)`,
    ).toContain(hash);
  });
});
