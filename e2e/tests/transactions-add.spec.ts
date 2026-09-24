import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Manual transaction entry: the third way a transaction can exist, alongside a
// statement import and a receipt. The sign convention is the thing worth
// guarding, so each flow checks the row renders as spend or as income.
//
// Self-cleaning: every row is E2E-prefixed and swept via the API afterwards, so
// the demo database ends each run exactly as it started. The UI has no per-row
// delete (only the MFA-gated mass delete), hence the API sweep.

const RUN = `E2E-${Date.now().toString(36)}`;

async function openAddPanel(page: Page) {
  await gotoPage(page, { route: "/transactions", heading: "Transactions" });
  await page.getByRole("button", { name: "➕ Add transaction" }).click();
  await expect(page.getByRole("button", { name: "Add transaction", exact: true })).toBeVisible();
}

// Narrow the list to the just-created row and return it.
async function findRow(page: Page, description: string) {
  await page.getByPlaceholder("Search description / merchant").fill(description);
  const row = page.locator("tr.txn-row").filter({ hasText: description });
  await expect(row).toHaveCount(1);
  return row;
}

test.describe("transactions: manual entry (self-cleaning)", () => {
  test.afterEach(async ({ request }) => {
    const res = await request.get(`/api/transactions?search=${RUN}&limit=100`);
    const body = (await res.json()) as { items: Array<{ id: number; description_raw: string }> };
    for (const t of body.items.filter((x) => x.description_raw.startsWith(RUN))) {
      await request.delete(`/api/transactions/${t.id}`);
    }
  });

  test("an expense is added as money out", async ({ page }) => {
    const description = `${RUN}-groceries`;
    await openAddPanel(page);

    await page.getByLabel("Amount").fill("120.25");
    await page.getByLabel("Description").fill(description);
    await page.getByRole("button", { name: "Add transaction", exact: true }).click();

    await expect(page.getByText(`Added ${description}`)).toBeVisible();

    const row = await findRow(page, description);
    // Stored negative, and rendered by the class that styles spend.
    await expect(row.locator("td.amt--neg")).toContainText("-120.25");
  });

  test("income is added as money in", async ({ page }) => {
    const description = `${RUN}-salary`;
    await openAddPanel(page);

    await page.getByRole("radio", { name: "Income (money in)" }).check();
    await page.getByLabel("Amount").fill("1500.00");
    await page.getByLabel("Description").fill(description);
    await page.getByRole("button", { name: "Add transaction", exact: true }).click();

    await expect(page.getByText(`Added ${description}`)).toBeVisible();

    const row = await findRow(page, description);
    await expect(row.locator("td.amt--pos")).toContainText("1500.00");
  });

  test("a chosen category sticks to the new row", async ({ page }) => {
    const description = `${RUN}-categorised`;
    await openAddPanel(page);

    const categorySelect = page.getByLabel("Category", { exact: true });
    await categorySelect.selectOption({ index: 1 }); // index 0 is "let the rules decide"
    const categoryName = await categorySelect.locator("option:checked").innerText();

    await page.getByLabel("Amount").fill("9.99");
    await page.getByLabel("Description").fill(description);
    await page.getByRole("button", { name: "Add transaction", exact: true }).click();
    await expect(page.getByText(`Added ${description}`)).toBeVisible();

    const row = await findRow(page, description);
    await expect(row).toContainText(categoryName);
  });

  test("the Dashboard quick-add link opens the panel ready to type", async ({ page }) => {
    await gotoPage(page, { route: "/", heading: "Dashboard" });
    await page.getByRole("link", { name: /Add transaction by hand/ }).click();

    await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
    // ?add=1 opens the panel, so the user lands on the form rather than the list.
    await expect(page.getByRole("button", { name: "Add transaction", exact: true })).toBeVisible();
    await expect(page.getByLabel("Amount")).toBeVisible();
  });
});
