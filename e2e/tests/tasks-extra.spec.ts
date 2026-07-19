import { test, expect, type Page } from "@playwright/test";
import { gotoPage } from "./helpers";

// Second batch of task flows: settings changes, user administration surface,
// error paths, and flows that pair UI actions with API-side verification or
// cleanup. Same rule as tasks.spec.ts: every mutating flow is self-cleaning.

const RUN = `E2EX-${Date.now().toString(36)}`;

async function confirmDialog(page: Page, label: RegExp | string) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

// Plain describe (not .serial): flows are independent and self-cleaning; one
// failure must not skip the rest (workers=1 already runs them one at a time).
test.describe("task flows: settings, users, error paths", () => {
  // Safety net: if a test fails mid-flow its inline cleanup never runs, so sweep
  // any stray E2E-prefixed accounts/tags via the API after every test. Keeps the
  // demo database pristine even across failed runs.
  test.afterEach(async ({ request }) => {
    const accounts = (await (await request.get("/api/accounts")).json()) as Array<{ id: number; name: string }>;
    for (const a of accounts.filter((x) => x.name.startsWith("E2E"))) {
      await request.delete(`/api/accounts/${a.id}`);
    }
    const tags = (await (await request.get("/api/tags")).json()) as Array<{ id: number; name: string }>;
    for (const t of tags.filter((x) => x.name.startsWith("E2E"))) {
      await request.delete(`/api/tags/${t.id}`);
    }
  });
  test("settings: theme switches to dark and back to system", async ({ page }) => {
    await gotoPage(page, { route: "/settings", heading: "Settings" });

    await page.getByRole("button", { name: /Dark/ }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    // Restore "System" (whatever it resolves to, the pref is back to default).
    await page.getByRole("button", { name: /System/ }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", /dark|light/);
  });

  test("projects: create with a budget, then delete", async ({ page }) => {
    const name = `${RUN}-project`;
    await gotoPage(page, { route: "/projects", heading: "Projects" });

    await page.getByPlaceholder("Name (e.g. Bathroom renovation)").fill(name);
    await page.getByPlaceholder(/^Budget/).fill("300");
    await page.getByRole("button", { name: "Add project" }).click();

    const row = page.locator(".project-list > div").filter({ hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "delete" }).click();
    await confirmDialog(page, /delete/i);
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test("accounts: create via the UI, verify, clean up via the API", async ({ page, request }) => {
    const name = `${RUN}-account`;
    await gotoPage(page, { route: "/accounts", heading: "Accounts" });

    await page.getByRole("button", { name: /New account/ }).click();
    await page.getByPlaceholder("Name", { exact: true }).fill(name);
    await page.getByRole("button", { name: "Add account" }).click();
    // The account list renders names in <strong>; the bare text also matches a
    // hidden <option> in an owner-select, so target the visible element.
    await expect(page.locator("strong").filter({ hasText: name })).toBeVisible();

    // No delete UI for accounts (only merge), so clean up over the API: a fresh
    // empty account deletes with 200/204 (409 would mean it somehow got data).
    const list = await (await request.get("/api/accounts")).json() as Array<{ id: number; name: string }>;
    const mine = list.find((a) => a.name === name);
    expect(mine, "created account is in the API list").toBeTruthy();
    const del = await request.delete(`/api/accounts/${mine!.id}`);
    expect([200, 204]).toContain(del.status());

    await page.reload();
    await expect(page.getByRole("heading", { name: "Accounts" }).first()).toBeVisible();
    await expect(page.locator("strong").filter({ hasText: name })).toHaveCount(0);
  });

  test("users: admin surface lists members with access-scope controls", async ({ page }) => {
    await gotoPage(page, { route: "/users", heading: "Users & access" });
    // Standalone runs as a bootstrapped local owner; user identities arrive via
    // HA ingress headers, so there is no create-user form here - assert the
    // management surface instead: at least one member row with a scope control.
    await expect(page.getByRole("button", { name: /all ▾/ }).first()).toBeVisible();
    await expect(page.getByText(/owner/i).first()).toBeVisible();
  });

  test("subscriptions: Detect now runs without an error banner", async ({ page }) => {
    await gotoPage(page, { route: "/subscriptions", heading: "Subscriptions" });
    await page.getByRole("button", { name: "Detect now" }).click();
    // Detection is idempotent on the same data; the page stays healthy and no
    // error status appears.
    await expect(page.locator(".status--error")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Subscriptions" })).toBeVisible();
  });

  test("import: a garbage 'CSV' fails gracefully with an error, no crash", async ({ page }) => {
    await gotoPage(page, { route: "/import", heading: "Import" });
    const jsErrors: string[] = [];
    page.on("pageerror", (e) => jsErrors.push(String(e)));

    await page.getByLabel(/Statement file to import/i).setInputFiles({
      name: "garbage.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("this is not, a real \x01\x02 statement\nat all;;;\n"),
    });
    await page.getByRole("button", { name: /^Preview$/ }).click();

    // A human-readable error (not a white screen, not an uncaught exception).
    await expect(page.locator(".status--error, [role=alert]").first()).toBeVisible();
    expect(jsErrors).toEqual([]);
    // The page is still usable afterwards.
    await expect(page.getByRole("button", { name: /^Preview$/ })).toBeVisible();
  });

  test("transactions: bulk '+ tag' opens the FE-10 prompt and cancels cleanly", async ({ page }) => {
    await gotoPage(page, { route: "/transactions", heading: "Transactions" });

    // Select the first row to reveal the bulk toolbar.
    await page.locator("td.col-select input[type=checkbox]").first().check();
    await page.getByRole("button", { name: "+ tag" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("textbox")).toBeVisible(); // it is a prompt, not just confirm
    await dialog.getByRole("button", { name: /cancel/i }).click();
    await expect(dialog).toBeHidden();
  });

  test("tags: an unused tag is removed end-to-end via the Tags page (#456)", async ({ page, request }) => {
    const name = `${RUN}-tag`;
    // Create an (unused) tag over the API, then remove it through the UI flow.
    const created = await request.post("/api/tags", { data: { name } });
    expect(created.status()).toBe(201);

    await gotoPage(page, { route: "/tags", heading: "Tags" });
    const card = page.locator(".card").filter({ hasText: "Merge duplicate tags" });
    // The tag name renders in the usage-list span AND in both merge <select>
    // options, so scope to the list span (not options) to avoid a strict-mode
    // hit; the exact count depends on a render race otherwise.
    await expect(card.locator("span").filter({ hasText: name }).first()).toBeVisible();

    await card.getByRole("button", { name: /Remove unused/i }).click();
    await confirmDialog(page, /Remove unused/i);

    // The result alert reports at least one removal; close it.
    const alert = page.getByRole("dialog");
    await expect(alert).toBeVisible();
    await expect(alert.getByText(/removed|deleted|unused/i)).toBeVisible();
    await alert.getByRole("button", { name: /ok|close/i }).click();

    await expect(card.getByText(name)).toHaveCount(0);
  });
});
