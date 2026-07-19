import { test, expect } from "@playwright/test";
import path from "node:path";
import { pathToFileURL } from "node:url";

// Guide section "Docs and architecture (browser check)": docs/architecture.html
// must render its diagrams as inline SVG with NO network access. We open the file
// directly over file:// (not through the app origin), fail on any off-origin
// request, and assert every diagram is present and visible.
const docUrl = pathToFileURL(
  path.join(__dirname, "..", "..", "docs", "architecture.html"),
).href;

test("architecture.html renders inline SVG diagrams with no network access", async ({ page }) => {
  // Any request to a real host (http/https) means the page is not self-contained.
  const external: string[] = [];
  page.on("request", (r) => {
    if (/^https?:/i.test(r.url())) external.push(r.url());
  });
  const jsErrors: string[] = [];
  page.on("pageerror", (e) => jsErrors.push(String(e)));

  await page.goto(docUrl);

  // The diagrams are inline <svg>; the doc ships six of them.
  const svgs = page.locator("svg");
  await expect(svgs.first()).toBeVisible();
  expect(await svgs.count(), "architecture doc should embed several inline SVG diagrams").toBeGreaterThanOrEqual(5);

  expect(external, "architecture.html must not fetch anything off-origin").toEqual([]);
  expect(jsErrors).toEqual([]);
});
