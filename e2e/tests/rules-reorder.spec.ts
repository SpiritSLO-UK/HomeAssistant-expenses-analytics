import { test, expect, type Page, type Locator } from "@playwright/test";
import { gotoPage } from "./helpers";

// Guide section "Rules: clone and drag-to-reorder" (#11). Clone is covered by
// tasks.spec.ts; here we verify drag-to-reorder and that the new order persists.
// Self-cleaning: creates two uniquely-named rules with the two highest priorities
// (so they sit adjacent at the top), reorders them, then deletes both.

const RUN = `E2ER-${Date.now().toString(36)}`;

async function confirmDialog(page: Page, label: RegExp | string) {
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: label }).click();
  await expect(dialog).toBeHidden();
}

// Create a rule via the New-rule form: description_contains <needle>, set_category
// (first real category), at an explicit high priority so ours sort to the top.
async function createRule(page: Page, needle: string, priority: number): Promise<void> {
  await page.getByPlaceholder("value", { exact: true }).fill(needle);
  const catSelect = page
    .locator("select")
    .filter({ has: page.locator('option:text-is("choose category…")') });
  await catSelect.selectOption({ index: 1 });
  // The New-rule form's priority input (label "prio").
  const prio = page.locator("label", { hasText: "prio" }).locator("input[type=number]");
  await prio.fill(String(priority));
  await page.getByRole("button", { name: "Create rule" }).click();
  await expect(page.locator("tbody tr").filter({ hasText: needle })).toHaveCount(1);
}

// Fire an HTML5 drag from one row onto another. The page's handlers track the
// dragged index via React state on dragstart and reorder on drop, so dispatching
// the events in sequence (each its own awaited call, letting React flush) is
// enough - no DataTransfer payload is read.
async function dragRowOnto(page: Page, source: Locator, target: Locator): Promise<void> {
  await source.dispatchEvent("dragstart");
  await target.dispatchEvent("dragover");
  await target.dispatchEvent("drop");
  await source.dispatchEvent("dragend");
}

// Indices of the two needles within the current rule table order.
async function orderOf(page: Page, a: string, b: string): Promise<{ ia: number; ib: number }> {
  const texts = await page.locator("table.table tbody tr").allTextContents();
  return {
    ia: texts.findIndex((t) => t.includes(a)),
    ib: texts.findIndex((t) => t.includes(b)),
  };
}

test.describe("rules: drag-to-reorder (self-cleaning)", () => {
  test("dragging a rule reorders it and the new priority persists (#11)", async ({ page }) => {
    const first = `${RUN}-first`;
    const second = `${RUN}-second`;

    await gotoPage(page, { route: "/rules", heading: "Rules" });
    await createRule(page, first, 995);
    await createRule(page, second, 990);

    // Higher priority sorts to the top: `first` (995) is above `second` (990).
    let order = await orderOf(page, first, second);
    expect(order.ia).toBeGreaterThanOrEqual(0);
    expect(order.ib).toBeGreaterThan(order.ia);

    // Drag `second` up onto `first` -> they swap.
    const rowFirst = page.locator("tbody tr").filter({ hasText: first });
    const rowSecond = page.locator("tbody tr").filter({ hasText: second });
    await dragRowOnto(page, rowSecond, rowFirst);

    // After the drop `second` sits above `first`. Poll a single predicate so
    // both indices are read from the same (post-reorder) snapshot.
    await expect
      .poll(async () => {
        const o = await orderOf(page, first, second);
        return o.ib < o.ia;
      })
      .toBe(true);

    // Persisted: the swapped order survives a reload. Wait for the rule rows to
    // re-render (the table loads async after the heading) before reading order.
    await page.reload();
    await expect(page.getByRole("heading", { name: "Rules" }).first()).toBeVisible();
    await expect(page.locator("tbody tr").filter({ hasText: first })).toBeVisible();
    await expect
      .poll(async () => {
        const o = await orderOf(page, first, second);
        return o.ia >= 0 && o.ib >= 0 && o.ib < o.ia;
      }, { message: "reordered priority persisted after reload" })
      .toBe(true);

    // Cleanup: delete both rules.
    for (const needle of [first, second]) {
      await page.locator("tbody tr").filter({ hasText: needle }).getByRole("button", { name: "Delete" }).click();
      await confirmDialog(page, "Delete");
      await expect(page.locator("tbody tr").filter({ hasText: needle })).toHaveCount(0);
    }
  });
});
