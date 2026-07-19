import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// The forecast/pace fields surfaced in batch 7 (#435/#438/#441) + the batched
// savings sparkline (#450).

test("budgets show the prorated pace signal (#435)", async ({ page }) => {
  await gotoPage(page, { route: "/budgets", heading: "Budgets" });
  await expect(
    page.getByText(/on pace|over pace|under pace|on track|near limit/i).first(),
  ).toBeVisible();
});

test("projects show the burn-down forecast (#438)", async ({ page }) => {
  await gotoPage(page, { route: "/projects", heading: "Projects" });
  // Expand the first project row (its label starts with the disclosure triangle).
  const expander = page.getByRole("button", { name: /^▸/ }).first();
  await expect(expander).toBeVisible(); // wait for the project list to render
  await expander.click();
  await expect(page.getByText(/Forecast:/i).first()).toBeVisible();
});

test("savings shows sparklines and a goals section (#441/#450)", async ({ page }) => {
  await gotoPage(page, { route: "/savings", heading: "Savings" });
  await expect(page.getByRole("heading", { name: /Goals/i })).toBeVisible();
  // The batched balance_series drives a collapsed-row sparkline (an inline svg).
  await expect(page.locator("svg").first()).toBeVisible();
});
