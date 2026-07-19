import { test, expect } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Savings: deposit/withdraw and goal forecast" (#10). The goal
// forecast is covered by forecasts.spec.ts; here we verify the deposit/withdraw
// confirmation STATES THE RESULTING BALANCE before applying. Non-destructive: it
// opens the confirm and cancels, so no snapshot is written.

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

test("deposit confirmation states the resulting balance, then cancels (#10)", async ({ page, request }) => {
  const accounts = (await (await request.get("/api/savings/accounts")).json()) as Array<{ name: string }>;
  test.skip(accounts.length === 0, "demo has no savings account to exercise");
  const name = accounts[0].name;

  await gotoPage(page, { route: "/savings", heading: "Savings" });

  // Expand the first account (deposit/withdraw live in the detail panel).
  await page.getByRole("button", { name: new RegExp(escapeRegExp(name)) }).first().click();

  const amount = page.getByPlaceholder(/^Amount \(/);
  await expect(amount).toBeVisible();
  await amount.fill("10");

  await page.getByRole("button", { name: /Deposit/ }).click();

  // The confirm modal spells out the new balance before anything is applied.
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(/^Deposit .+\?/)).toBeVisible();
  await expect(dialog.getByText(/New balance:/)).toBeVisible();

  // Cancel: nothing is recorded (non-destructive).
  await dialog.getByRole("button", { name: /cancel/i }).click();
  await expect(dialog).toBeHidden();
});
