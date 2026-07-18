import { test, expect } from "@playwright/test";
import path from "node:path";
import { gotoPage } from "./helpers";

const fixture = path.join(__dirname, "..", "fixtures", "us-sample.csv");

// US month-first dates once misparsed ("unrecognised date: '6/28/2026'"). This
// drives the real import UI with a US-format CSV and asserts the preview parses
// 6/28/2026 as 28 June 2026. Non-destructive: it previews only, never confirms,
// so no rows are written to the demo database.
test("import: a US-format CSV previews with month-first dates parsed (#219/#348)", async ({ page }) => {
  await gotoPage(page, { route: "/import", heading: "Import" });

  await page
    .getByLabel(/Statement file to import/i)
    .setInputFiles(fixture);

  await page.getByRole("button", { name: /^Preview$/i }).click();

  // The preview renders the parsed rows; 6/28/2026 must become the ISO 2026-06-28
  // (not 2026-28-06 or a parse error).
  await expect(page.getByText("2026-06-28")).toBeVisible();
  await expect(page.getByText(/Whole Foods Market/i)).toBeVisible();
  // No parse-error banner.
  await expect(page.getByText(/unrecognised date|could not parse/i)).toHaveCount(0);
});
