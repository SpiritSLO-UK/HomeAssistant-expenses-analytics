import { test, expect } from "@playwright/test";
import path from "node:path";
import { gotoPage } from "./helpers";

// Guide section "Import: custom CSV column mapping and US-format dates" (#19). The
// "Map columns (custom CSV)" panel maps arbitrary bank columns and a date-order
// selector supports US month-first dates. Non-destructive: previews only, never
// confirms, so no rows are written. Uses the repo's ready-made US Chase sample
// (two date columns + a signed Amount).
const sample = path.join(__dirname, "..", "..", "examples", "sample-csv", "us-chase-sample.csv");

test("custom CSV mapping previews US month-first dates correctly (#19)", async ({ page }) => {
  await gotoPage(page, { route: "/import", heading: "Import" });

  await page.getByLabel(/Statement file to import/i).setInputFiles(sample);
  await page.getByRole("button", { name: /Map columns \(custom CSV\)/ }).click();

  const panel = page.locator(".card").filter({ hasText: "Map columns (custom CSV)" });
  const mapTable = panel
    .locator("table")
    .filter({ has: page.getByRole("columnheader", { name: "Field" }) });
  await expect(mapTable).toBeVisible();

  // The date-order selector offers exactly Auto / Day-first / Month-first.
  const dateOrder = panel.locator("label").filter({ hasText: "Date format" }).locator("select");
  await expect(dateOrder.locator("option")).toHaveText([
    "Auto-detect",
    "Day-first DD/MM",
    "Month-first MM/DD",
  ]);

  // Map each field from the file's headers.
  const fieldRow = (label: string) =>
    mapTable.locator("tbody tr").filter({ has: page.locator("td > div", { hasText: new RegExp(`^${label}`) }) });
  await fieldRow("Date").locator("select").selectOption({ label: "Transaction Date" });
  await fieldRow("Amount").locator("select").selectOption({ label: "Amount" });
  await fieldRow("Description").locator("select").selectOption({ label: "Description" });

  // Month-first: 6/28/2026 must become 28 June 2026, not month 28 or a parse error.
  await dateOrder.selectOption("mdy");
  await page.getByRole("button", { name: "Preview with this mapping" }).click();

  await expect(page.getByText("2026-06-28")).toBeVisible();
  await expect(page.getByText(/unrecognised date|could not parse/i)).toHaveCount(0);
});
