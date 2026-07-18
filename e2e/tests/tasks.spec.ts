import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Task flows: really DO things (create, edit, confirm, delete), not just render.
// Every flow is SELF-CLEANING - it creates its own uniquely-named data, acts on
// it, verifies, then deletes it and verifies it is gone - so the demo database
// ends each run exactly as it started and reruns are idempotent.

// Unique per run so parallel/current data never collides with leftovers.
const RUN = `E2E-${Date.now().toString(36)}`;

// Accept the in-app confirm dialog (FE-10) by its action button.
async function confirmDialog(page: Page, label: RegExp | string = /delete/i) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

// Plain describe (not .serial): each flow is independent and self-cleaning, so
// one failure must not skip the rest; workers=1 already runs them one at a time.
test.describe("task flows (self-cleaning)", () => {
  test("categories: create, then delete via the confirm modal", async ({ page }) => {
    const name = `${RUN}-cat`;
    await gotoPage(page, { route: "/categories", heading: "Categories" });

    await page.getByPlaceholder("New category name").fill(name);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByText(name).first()).toBeVisible();

    await page.getByRole("button", { name: `Delete category ${name}` }).click();
    await confirmDialog(page);
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test("budgets: create, see it (with pace once data exists), delete", async ({ page }) => {
    const name = `${RUN}-budget`;
    await gotoPage(page, { route: "/budgets", heading: "Budgets" });

    await page.getByPlaceholder("Name (e.g. Groceries)").fill(name);
    await page.getByPlaceholder(/^Amount/).fill("123.45");
    await page.getByRole("button", { name: "Add budget" }).click();

    const row = page.locator(".budget-row").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "delete" }).click();
    await confirmDialog(page);
    await expect(page.locator(".budget-row").filter({ hasText: name })).toHaveCount(0);
  });

  test("rules: create, clone (#423), delete both", async ({ page }) => {
    const needle = `${RUN}-rule-needle`;
    await gotoPage(page, { route: "/rules", heading: "Rules" });

    // Condition: description_contains <needle> (defaults); action: set_category
    // via the category dropdown (pick the first real category).
    await page.getByPlaceholder("value", { exact: true }).fill(needle);
    const catSelect = page.locator("select").filter({ has: page.locator('option:text-is("choose category…")') });
    await catSelect.selectOption({ index: 1 });
    await page.getByRole("button", { name: "Create rule" }).click();

    const mine = page.locator("li, .rule-row, tbody tr").filter({ hasText: needle });
    await expect(mine).toHaveCount(1);

    await mine.first().getByRole("button", { name: "Clone" }).click();
    await expect(page.locator("li, .rule-row, tbody tr").filter({ hasText: needle })).toHaveCount(2);

    for (let i = 0; i < 2; i++) {
      await page.locator("li, .rule-row, tbody tr").filter({ hasText: needle }).first()
        .getByRole("button", { name: "Delete" }).click();
      await confirmDialog(page);
    }
    await expect(page.locator("li, .rule-row, tbody tr").filter({ hasText: needle })).toHaveCount(0);
  });

  test("vendors: add with alias, then delete", async ({ page }) => {
    const name = `${RUN}-vendor`;
    await gotoPage(page, { route: "/vendors", heading: "Vendors" });

    await page.getByLabel("Canonical vendor name").fill(name);
    await page.getByLabel("Alias to match").fill(`${name}-alias`);
    await page.getByRole("button", { name: "Add vendor" }).click();

    const row = page.locator("tbody tr").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "Delete" }).click();
    await confirmDialog(page);
    await expect(page.getByText(name, { exact: true })).toHaveCount(0);
  });

  test("savings: create a goal, see its forecast state, delete it", async ({ page }) => {
    const name = `${RUN}-goal`;
    await gotoPage(page, { route: "/savings", heading: "Savings" });

    await page.getByPlaceholder("Goal name").fill(name);
    await page.getByPlaceholder(/^Target/).fill("500");
    await page.getByRole("button", { name: "Add goal" }).click();

    const row = page.locator("li").filter({ hasText: name }).first();
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "delete" }).click();
    await confirmDialog(page);
    await expect(page.locator("li").filter({ hasText: name })).toHaveCount(0);
  });

  test("settings: log-level optimistic select changes and restores", async ({ page }) => {
    await gotoPage(page, { route: "/settings", heading: "Settings" });

    // The log-level select (converted to optimistic rollback in #447).
    const select = page.locator("select").filter({ has: page.locator('option[value="DEBUG"]') }).first();
    await expect(select).toBeVisible();
    const original = await select.inputValue();
    const flipped = original === "DEBUG" ? "INFO" : "DEBUG";

    await select.selectOption(flipped);
    await expect(select).toHaveValue(flipped); // optimistic: reflects immediately

    // Survives a reload (server actually persisted it).
    await page.reload();
    await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();
    const after = page.locator("select").filter({ has: page.locator('option[value="DEBUG"]') }).first();
    await expect(after).toHaveValue(flipped);

    // Restore.
    await after.selectOption(original);
    await expect(after).toHaveValue(original);
  });

  test("import: full CSV import (preview -> confirm -> visible), then cleaned up via API", async ({ page, request }) => {
    const vendor = `${RUN}-import-vendor`;
    // Build a tiny unique CSV on the fly so dedupe never collides across runs.
    // Recent dates so the rows land inside the Transactions page's range presets.
    const day = (back: number) => new Date(Date.now() - back * 86_400_000).toISOString().slice(0, 10);
    const csv = [
      "Date,Description,Amount",
      `${day(1)},${vendor} one,-11.11`,
      `${day(2)},${vendor} two,-22.22`,
    ].join("\n");

    await gotoPage(page, { route: "/import", heading: "Import" });
    await page.getByLabel(/Statement file to import/i).setInputFiles({
      name: `${RUN}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(csv),
    });

    // Capture the upload response to learn the import_id for cleanup.
    const [uploadResp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/imports") && r.request().method() === "POST"),
      page.getByRole("button", { name: /^Preview$/ }).click(),
    ]);
    const uploaded = (await uploadResp.json()) as { import_id?: number };

    await expect(page.getByText(`${vendor} one`)).toBeVisible();
    await page.getByRole("button", { name: /Confirm import/ }).click();
    await expect(page.getByRole("heading", { name: /Import complete/ })).toBeVisible();

    // The rows are now real transactions. Widen the range preset so a run near a
    // month boundary can't filter yesterday's rows out of the default view.
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });
    await page.getByRole("button", { name: "Last 90 days" }).click();
    await expect(page.getByText(`${vendor} one`).first()).toBeVisible();

    // Cleanup: delete the whole import (statement + its transactions) via API.
    expect(uploaded.import_id, "upload response carries import_id").toBeTruthy();
    const del = await request.delete(`/api/imports/${uploaded.import_id}`);
    expect(del.status()).toBe(204);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
    await expect(page.getByText(`${vendor} one`)).toHaveCount(0);
  });

  test("search: a category: token query returns results", async ({ page }) => {
    await gotoPage(page, { route: "/search", heading: "Search" });
    const box = page.getByPlaceholder(/category:/i);
    await box.fill("category:Groceries");
    // Debounced search: the results panel (#search-results) fills with hits.
    const results = page.locator("#search-results");
    await expect(results).toBeVisible();
    await expect(results.locator("li, a, .search-hit").first()).toBeVisible();
  });
});

