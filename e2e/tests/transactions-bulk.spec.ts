import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import { gotoPage } from "./helpers";

// Guide sections: optimistic select-on-change (#3), CSV export of the filtered
// set vs the selected rows (#4), and inline / bulk tags (#5). Every mutating
// flow either restores what it changed or cleans up via the API afterEach, so
// the demo database ends each run as it started.

const RUN = `E2ET-${Date.now().toString(36)}`;

// Count CSV *records* (header + data), respecting quoted fields that may embed
// commas or newlines - so a description with a comma never inflates the count.
function countCsvRecords(text: string): number {
  let records = 0;
  let inQuotes = false;
  let hasContent = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') {
      if (inQuotes && text[i + 1] === '"') i++; // escaped quote
      else inQuotes = !inQuotes;
      hasContent = true;
    } else if (ch === "\n" && !inQuotes) {
      if (hasContent) records++;
      hasContent = false;
    } else if (ch !== "\r") {
      hasContent = true;
    }
  }
  if (hasContent) records++;
  return records;
}

async function readDownloadCsv(page: Page, trigger: Promise<unknown>): Promise<string> {
  const [download] = await Promise.all([page.waitForEvent("download"), trigger]);
  expect(download.suggestedFilename()).toContain("transactions");
  const path = await download.path();
  expect(path, "download has a local path").toBeTruthy();
  return fs.readFileSync(path!, "utf8");
}

// Expand the first transaction row's detail panel (holds the per-row selects and
// the Tags field) and return that first row's checkbox locator too.
async function openFirstRow(page: Page) {
  const toggle = page.locator("button.txn-row__toggle").first();
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator("tr.txn-detail").first()).toBeVisible();
}

test.describe("transactions: optimistic selects, CSV export, tags", () => {
  // Any tag these flows create is E2E-prefixed; sweep them (and their row
  // associations) via the API so the demo stays pristine even after a failure.
  test.afterEach(async ({ request }) => {
    const tags = (await (await request.get("/api/tags")).json()) as Array<{ id: number; name: string }>;
    for (const t of tags.filter((x) => x.name.startsWith("E2E"))) {
      await request.delete(`/api/tags/${t.id}`);
    }
  });

  test("per-row category select applies optimistically and persists (#48)", async ({ page }) => {
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });
    await openFirstRow(page);

    const field = page.locator(".txn-detail__field").filter({ hasText: "Category" });
    const select = field.getByRole("combobox");
    await expect(select).toBeVisible();

    const original = await select.inputValue();
    // Pick an option that differs from the current value, preferring a real
    // category (non-empty) so the change is visible; "" (uncategorised) is a
    // valid fallback and a legitimate value in its own right.
    const values = await select.locator("option").evaluateAll((opts) =>
      (opts as HTMLOptionElement[]).map((o) => o.value),
    );
    const others = values.filter((v) => v !== original);
    expect(others.length, "a different category option exists").toBeGreaterThan(0);
    const target = others.find((v) => v !== "") ?? others[0];

    await select.selectOption(target);
    await expect(select).toHaveValue(target); // optimistic: reflects immediately

    // Persisted server-side: survives a reload.
    await page.reload();
    await openFirstRow(page);
    const after = page.locator(".txn-detail__field").filter({ hasText: "Category" }).getByRole("combobox");
    await expect(after).toHaveValue(target);

    // Restore the original value so the demo data is unchanged.
    await after.selectOption(original);
    await expect(after).toHaveValue(original);
  });

  test("bulk value-change asks 'Apply ... to N transaction(s)?' first (#3)", async ({ page }) => {
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });

    const boxes = page.locator("td.col-select input[type=checkbox]");
    await boxes.nth(0).check();
    await boxes.nth(1).check();

    const bar = page.locator(".bulk-bar");
    await expect(bar).toBeVisible();
    await expect(bar.getByText(/2 selected/)).toBeVisible();

    // Choosing a category in the bulk select triggers the confirm before applying.
    await page.getByTitle("Set category for selected").selectOption({ index: 1 });

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/to 2 transaction\(s\)\?/i)).toBeVisible();

    // Cancel: nothing is applied (non-destructive).
    await dialog.getByRole("button", { name: /cancel/i }).click();
    await expect(dialog).toBeHidden();
    await bar.getByRole("button", { name: /^Clear$/ }).click();
    await expect(bar).toBeHidden();
  });

  test("export CSV: selected rows only vs the whole filtered set (#4)", async ({ page }) => {
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });
    // Widen the range so there is a healthy set to export.
    await page.getByRole("button", { name: "This year" }).click();
    await expect(page.locator("button.txn-row__toggle").first()).toBeVisible();

    // (a) Nothing selected -> the filtered set. Must be at least the visible page.
    const visibleRows = await page.locator("tr.txn-row").count();
    const exportBtn = page.getByRole("button", { name: /Export/ });
    const filteredCsv = await readDownloadCsv(page, exportBtn.click());
    const filteredData = countCsvRecords(filteredCsv) - 1; // minus header
    expect(filteredData).toBeGreaterThanOrEqual(visibleRows);
    expect(filteredData).toBeGreaterThan(0);

    // (b) Tick exactly two rows -> the export contains only those two.
    const boxes = page.locator("td.col-select input[type=checkbox]");
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await expect(page.locator(".bulk-bar")).toBeVisible();
    const selectedBtn = page.getByRole("button", { name: /Export 2 selected/ });
    const selectedCsv = await readDownloadCsv(page, selectedBtn.click());
    expect(countCsvRecords(selectedCsv) - 1).toBe(2);
  });

  test("tags: add and remove inline on a single row (#5)", async ({ page }) => {
    const tag = `${RUN}-inline`;
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });
    await openFirstRow(page);

    const tagsField = page.locator(".txn-detail__field").filter({ hasText: "Tags" });
    await tagsField.getByRole("button", { name: "+ tag" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("textbox").fill(tag);
    await dialog.getByRole("button", { name: /^Add$/ }).click();

    const chip = tagsField.getByRole("button", { name: new RegExp(`${tag}`) });
    await expect(chip).toBeVisible();

    // The chip itself is the remove control.
    await chip.click();
    await expect(tagsField.getByRole("button", { name: new RegExp(`${tag}`) })).toHaveCount(0);
  });

  test("tags: bulk '+ tag' applies to every selected row (#5)", async ({ page }) => {
    const tag = `${RUN}-bulk`;
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });

    const boxes = page.locator("td.col-select input[type=checkbox]");
    await boxes.nth(0).check();
    await boxes.nth(1).check();

    const bar = page.locator(".bulk-bar");
    await expect(bar).toBeVisible();
    await bar.getByRole("button", { name: "+ tag" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/2 selected transactions/i)).toBeVisible();
    await dialog.getByRole("textbox").fill(tag);
    await dialog.getByRole("button", { name: /^Add$/ }).click();
    await expect(dialog).toBeHidden();

    // The tag now shows on both selected rows (display chips in the Flags column).
    await expect(page.locator("span.tag").filter({ hasText: tag })).toHaveCount(2);
    // Cleanup happens in afterEach (deletes the E2E tag + its associations).
  });
});
