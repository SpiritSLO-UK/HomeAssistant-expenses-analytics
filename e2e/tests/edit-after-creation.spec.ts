import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Edit after creation (projects, budgets, rules, savings goals)"
// (#15). Each entity is created, edited, verified to persist, then removed - so
// the demo database ends each run as it started. The savings-account case edits
// an existing demo account (there is no delete endpoint) and restores it via the
// API, guaranteeing cleanup even on failure.

const RUN = `E2EE-${Date.now().toString(36)}`;

async function confirmDialog(page: Page, label: RegExp | string) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test.describe("edit after creation (self-cleaning)", () => {
  test("projects: edit a project's budget and it persists", async ({ page }) => {
    const name = `${RUN}-project`;
    await gotoPage(page, { route: "/projects", heading: "Projects" });

    await page.getByPlaceholder("Name (e.g. Bathroom renovation)").fill(name);
    await page.getByPlaceholder(/^Budget/).fill("300");
    await page.getByRole("button", { name: "Add project" }).click();

    const row = () => page.locator(".project-list > div").filter({ hasText: name });
    await expect(row()).toBeVisible();

    await row().getByRole("button", { name: "edit" }).click();
    await page.getByLabel("Project budget").fill("450");
    await page.getByRole("button", { name: "Save changes" }).click();

    // Persisted: reopen the edit form after a reload. The stored value comes back
    // as a 2dp decimal string ("450.00").
    await page.reload();
    await expect(page.getByRole("heading", { name: "Projects" }).first()).toBeVisible();
    await row().getByRole("button", { name: "edit" }).click();
    await expect(page.getByLabel("Project budget")).toHaveValue(/^450(\.00)?$/);

    // Cleanup.
    await row().getByRole("button", { name: "delete" }).click();
    await confirmDialog(page, /delete/i);
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test("budgets: edit a budget's amount and it persists", async ({ page }) => {
    const name = `${RUN}-budget`;
    await gotoPage(page, { route: "/budgets", heading: "Budgets" });

    await page.getByPlaceholder("Name (e.g. Groceries)").fill(name);
    await page.getByPlaceholder(/^Amount/).fill("123.45");
    await page.getByRole("button", { name: "Add budget" }).click();

    const row = () => page.locator(".budget-row").filter({ hasText: name });
    await expect(row()).toBeVisible();

    await row().getByRole("button", { name: "edit" }).click();
    await page.getByLabel("Amount", { exact: true }).fill("222.22");
    await page.getByRole("button", { name: "Save changes" }).click();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Budgets" }).first()).toBeVisible();
    await row().getByRole("button", { name: "edit" }).click();
    await expect(page.getByLabel("Amount", { exact: true })).toHaveValue("222.22");

    // Cleanup.
    await row().getByRole("button", { name: "delete" }).click();
    await confirmDialog(page, /delete/i);
    await expect(page.locator(".budget-row").filter({ hasText: name })).toHaveCount(0);
  });

  test("rules: edit a rule's name and it persists", async ({ page }) => {
    const needle = `${RUN}-rule-needle`;
    const newName = `${RUN}-rule-renamed`;
    await gotoPage(page, { route: "/rules", heading: "Rules" });

    await page.getByPlaceholder("value", { exact: true }).fill(needle);
    const catSelect = page
      .locator("select")
      .filter({ has: page.locator('option:text-is("choose category…")') });
    await catSelect.selectOption({ index: 1 });
    await page.getByRole("button", { name: "Create rule" }).click();

    const row = () => page.locator("tbody tr").filter({ hasText: needle });
    await expect(row()).toHaveCount(1);

    await row().getByRole("button", { name: "Edit" }).click();
    await page.getByPlaceholder("rule name").fill(newName);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("tbody tr").filter({ hasText: newName })).toHaveCount(1);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Rules" }).first()).toBeVisible();
    await expect(page.locator("tbody tr").filter({ hasText: newName })).toHaveCount(1);

    // Cleanup (the condition still carries the needle).
    await row().getByRole("button", { name: "Delete" }).click();
    await confirmDialog(page, "Delete");
    await expect(page.locator("tbody tr").filter({ hasText: needle })).toHaveCount(0);
  });

  test("savings goals: edit a goal's target and it persists", async ({ page }) => {
    const name = `${RUN}-goal`;
    await gotoPage(page, { route: "/savings", heading: "Savings" });

    await page.getByPlaceholder("Goal name").fill(name);
    await page.getByPlaceholder(/^Target/).fill("500");
    await page.getByRole("button", { name: "Add goal" }).click();

    const li = () => page.locator("li").filter({ hasText: name }).first();
    await expect(li()).toBeVisible();

    await li().getByRole("button", { name: "edit" }).click();
    await li().getByPlaceholder(/^Target/).fill("750");
    await li().getByRole("button", { name: "Save goal" }).click();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Savings" }).first()).toBeVisible();
    await li().getByRole("button", { name: "edit" }).click();
    await expect(li().getByPlaceholder(/^Target/)).toHaveValue(/^750(\.00)?$/);

    // Cleanup.
    await li().getByRole("button", { name: "delete" }).click();
    await confirmDialog(page, /delete/i);
    await expect(page.locator("li").filter({ hasText: name })).toHaveCount(0);
  });

  test("savings accounts: edit name (currency stays read-only), restored via API", async ({ page, request }) => {
    const accounts = (await (await request.get("/api/savings/accounts")).json()) as Array<{ id: number; name: string }>;
    test.skip(accounts.length === 0, "demo has no savings account to edit");
    const acct = accounts[0];
    const temp = `${acct.name} E2Eedit`;

    await gotoPage(page, { route: "/savings", heading: "Savings" });
    await page.getByRole("button", { name: new RegExp(escapeRegExp(acct.name)) }).first().click();

    await page.getByRole("button", { name: "✎ Edit details" }).click();
    const nameInput = page.getByPlaceholder("Account name");
    await expect(nameInput).toHaveValue(acct.name);

    // Currency is read-only: a "(fixed)" note, and no currency input to change.
    await expect(page.getByText(/Currency .+ \(fixed\)/)).toBeVisible();

    await nameInput.fill(temp);
    // Wait for the save to actually reach the server before reading it back.
    const [saveResp] = await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes(`/api/savings/accounts/${acct.id}`) && r.request().method() === "PATCH",
      ),
      page.getByRole("button", { name: "Save details" }).click(),
    ]);
    expect(saveResp.ok(), "save-details PATCH succeeded").toBeTruthy();

    // Read back via the API, then restore immediately so cleanup is guaranteed
    // (there is no delete endpoint for savings accounts) before we assert.
    const after = ((await (await request.get("/api/savings/accounts")).json()) as Array<{ id: number; name: string }>)
      .find((a) => a.id === acct.id);
    await request.patch(`/api/savings/accounts/${acct.id}`, { data: { name: acct.name } });

    expect(after?.name, "account name edit persisted").toBe(temp);
  });
});
