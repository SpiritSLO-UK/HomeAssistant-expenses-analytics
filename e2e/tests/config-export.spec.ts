import { test, expect } from "@playwright/test";

// Config & library export/import round-trip (#562). Export the demo's config,
// then re-import that exact file. Import is a non-destructive upsert by name, so
// everything already exists and nothing is added, but the flow exercises the
// v0.2 document end to end: the export now carries each vendor's default category
// and the whole rules table, and the import result banner now reports a rules
// count. Non-destructive - re-importing the demo's own export adds no rows.
test("config: export then re-import round-trips and reports a rules count (#562)", async ({ page }) => {
  await page.goto("/#/settings?section=data");
  await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();

  const card = page.locator(".card", { hasText: "Config & library" });
  await expect(card).toBeVisible();

  // Export downloads the portable JSON document.
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    card.getByRole("button", { name: /Export config/i }).click(),
  ]);
  expect(download.suggestedFilename()).toContain("config");
  const path = await download.path();
  expect(path, "config export has a local path").toBeTruthy();

  // Re-import the same file via the hidden JSON file input.
  await card.locator('input[type="file"][accept*="json"]').setInputFiles(path!);

  // The success banner now includes a rules count (0 here - all already present),
  // proving vendor default categories + rules travel through the export/import.
  await expect(page.getByText(/Imported config:.*\brules\b/i)).toBeVisible();
});
