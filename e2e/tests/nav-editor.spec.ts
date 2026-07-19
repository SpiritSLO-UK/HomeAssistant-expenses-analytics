import { test, expect, type Page } from "@playwright/test";

// Navigation editor (grouped-nav PR3). The sidebar footer's "Customise navigation"
// button opens a modal editor operating on a working copy of the layout; every
// change is debounced-saved through the self-service PR1 API and the live sidebar
// + page sub-tabs update. These tests drive the accessible controls (checkboxes,
// ▲▼ move buttons, the "Move to…" select) — the touch-friendly path that is also
// the most robust to automate — and always leave the layout reset to default.

async function openEditor(page: Page) {
  await page.getByRole("button", { name: /Customise navigation/ }).click();
  const dialog = page.getByRole("dialog", { name: "Customise navigation" });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function closeEditor(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Customise navigation" });
  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(dialog).toBeHidden();
}

// Restore the built-in default layout so specs don't leak state into each other
// (the suite runs serially against one shared backend).
test.afterEach(async ({ page }) => {
  await page.goto("/#/import");
  await openEditor(page);
  const dialog = page.getByRole("dialog", { name: "Customise navigation" });
  await dialog.getByRole("button", { name: "Reset to default" }).click();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);
});

test("nav editor: hide then re-show a page removes/restores its sub-tab", async ({ page }) => {
  await page.goto("/#/import");
  const tablist = page.getByRole("tablist");
  await expect(tablist.getByRole("tab", { name: /Receipts/ })).toBeVisible();

  const dialog = await openEditor(page);
  await dialog.getByRole("checkbox", { name: "Show Receipts" }).uncheck();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);

  // The hidden page drops out of the group's sub-tabs (still route-reachable).
  await expect(page.getByRole("tablist").getByRole("tab", { name: /Receipts/ })).toHaveCount(0);

  const dialog2 = await openEditor(page);
  await dialog2.getByRole("checkbox", { name: "Show Receipts" }).check();
  await expect(dialog2.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);

  await expect(page.getByRole("tablist").getByRole("tab", { name: /Receipts/ })).toBeVisible();
});

test("nav editor: reorder items within a group changes the sub-tab order", async ({ page }) => {
  await page.goto("/#/import");
  // Default Money order → first sub-tab is Import.
  await expect(page.getByRole("tablist").getByRole("tab").first()).toHaveText(/Import/);

  const dialog = await openEditor(page);
  await dialog.getByRole("button", { name: "Move Transactions up" }).click();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);

  // Transactions moved to the top → it is now the first sub-tab.
  await expect(page.getByRole("tablist").getByRole("tab").first()).toHaveText(/Transactions/);
});

test("nav editor: move a page from one group to another", async ({ page }) => {
  await page.goto("/#/import");
  const dialog = await openEditor(page);
  // Move Receipts out of Money into the Library group.
  await dialog.getByRole("combobox", { name: "Move Receipts to group" }).selectOption({ label: "Library" });
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);

  // Receipts is gone from Money's sub-tabs...
  await expect(page.getByRole("tablist").getByRole("tab", { name: /Receipts/ })).toHaveCount(0);
  // ...and now appears as a sub-tab under a Library page.
  await page.goto("/#/categories");
  await expect(page.getByRole("tablist").getByRole("tab", { name: /Receipts/ })).toBeVisible();
});

test("nav editor: create a custom group", async ({ page }) => {
  await page.goto("/#/import");
  const dialog = await openEditor(page);
  await dialog.getByRole("textbox", { name: "New group name" }).fill("My Zone");
  await dialog.getByRole("button", { name: "Add group" }).click();

  // The new custom group shows its editable name field + a delete affordance
  // (only custom groups can be deleted).
  await expect(dialog.getByRole("textbox", { name: /Group name for My Zone/ })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Delete group My Zone" })).toBeVisible();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);
});

test("nav editor: reset to default restores a hidden page", async ({ page }) => {
  await page.goto("/#/import");
  const dialog = await openEditor(page);
  await dialog.getByRole("checkbox", { name: "Show Receipts" }).uncheck();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await dialog.getByRole("button", { name: "Reset to default" }).click();
  await expect(dialog.getByText(/Saved/)).toBeVisible();
  await closeEditor(page);

  // Default layout is back → Receipts sub-tab present again.
  await expect(page.getByRole("tablist").getByRole("tab", { name: /Receipts/ })).toBeVisible();
});
