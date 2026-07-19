import { test, expect, type Page } from "@playwright/test";
import { PERIOD, secondsIntoPeriod, totpCode } from "./totp";

// ui-test-guide §14: automate the MFA backup-codes flow that used to be a manual
// QA step. Standalone (the CI target) runs as a local owner with no login, so the
// spec can enable MFA for that owner, generate backup codes, and tear it back down.
//
// How a valid code is obtained in-test: the POST /api/auth/mfa/setup response
// carries the raw base32 TOTP secret, and totp.ts recomputes the current 6-digit
// code from it (RFC 6238) — no TOTP dependency, no interactive enrolment.
//
// The suite runs SERIALLY against one shared backend, so this spec MUST leave MFA
// off. Enabling it otherwise gates every subsequent request behind a code prompt
// and breaks the rest of the run. afterEach disables MFA no matter how the test
// ends (via the disable endpoint, with a freshly computed code).

// The base32 secret captured from /setup; also used by the cleanup hook.
let mfaSecret: string | null = null;

// Give the current 30s window enough runway that one code stays valid across the
// back-to-back enable -> verify (and, if prompted, the step-up). The backend
// accepts +/-1 period, so this only ever waits a few seconds near a boundary.
async function codeWithRunway(page: Page): Promise<string> {
  const into = secondsIntoPeriod();
  if (into > PERIOD - 5) {
    await page.waitForTimeout((PERIOD - into + 1) * 1000);
  }
  return totpCode(mfaSecret as string);
}

test.afterEach(async ({ page }) => {
  // Reset the shared backend: turn MFA back off. disable() takes a current TOTP
  // code and is not step-up gated, so a plain POST with a fresh code is enough.
  // Best-effort: if the test never enabled MFA this 400s and is ignored.
  if (mfaSecret) {
    try {
      await page.request.post("api/auth/mfa/disable", {
        data: { code: totpCode(mfaSecret) },
        headers: { "Content-Type": "application/json" },
      });
    } catch {
      // ignore — nothing must fail the run in teardown
    }
  }
  mfaSecret = null;
  // Drop any MFA session token this browser stored, so state is fully clean.
  await page
    .evaluate(() => {
      try {
        globalThis.sessionStorage.removeItem("hafi_session");
        globalThis.localStorage.removeItem("hafi_session");
      } catch {
        /* storage may be unavailable */
      }
    })
    .catch(() => {});
});

test("MFA backup codes: enable, generate, download, then disable (ui-test-guide §14)", async ({
  page,
}) => {
  // 1. Land directly on Settings -> Security via the deep-link.
  await page.goto("/#/settings?section=security");
  await expect(page.getByRole("heading", { name: "Settings" }).first()).toBeVisible();
  const tablist = page.getByRole("tablist", { name: "Settings sections" });
  await expect(tablist.getByRole("tab", { name: /Security/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const mfaCard = page
    .locator(".card")
    .filter({ has: page.getByRole("heading", { name: /Two-factor authentication/i }) });
  await expect(mfaCard).toBeVisible();

  // 2. Trigger setup and read the secret straight off the API response.
  const [setupRes] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/auth/mfa/setup") && r.request().method() === "POST",
    ),
    mfaCard.getByRole("button", { name: /set up two-factor/i }).click(),
  ]);
  expect(setupRes.ok()).toBeTruthy();
  const setupBody = (await setupRes.json()) as { secret?: string };
  mfaSecret = setupBody.secret ?? null;
  expect(mfaSecret, "the /setup response must expose the base32 secret").toBeTruthy();

  // The enrolment form (with the secret) is now shown.
  const codeInput = mfaCard.locator('input[name="mfa-enable-code"]');
  await expect(codeInput).toBeVisible();

  // 3. Confirm & enable with a computed code. Retry once across a fresh window if
  //    the code is rejected at a period boundary (the form stays put on error).
  const enabledStatus = page.getByText(/Two-factor is enabled/i);
  const enableError = mfaCard.locator(".status--error");
  for (let attempt = 0; attempt < 2; attempt++) {
    await codeInput.fill(await codeWithRunway(page));
    await mfaCard.getByRole("button", { name: /confirm & enable/i }).click();
    await expect(enabledStatus.or(enableError).first()).toBeVisible();
    if (await enabledStatus.isVisible()) break;
    expect(attempt, "MFA enable was rejected twice — code timing").toBeLessThan(1);
    // Wait out the current window, then the loop recomputes a fresh code.
    await page.waitForTimeout((PERIOD - secondsIntoPeriod() + 1) * 1000);
  }
  await expect(enabledStatus).toBeVisible();

  // 4. The backup-codes section appears now that MFA is on. Start with none.
  await expect(page.getByRole("heading", { name: /^Backup codes$/ })).toBeVisible();
  await expect(page.getByText(/No backup codes yet/i)).toBeVisible();

  // Generate a set. The enable flow just verified, so the session is freshly
  // stepped-up and generation should not prompt — but handle a step-up prompt
  // defensively so a slow run can't flake.
  await page.getByRole("button", { name: /generate backup codes/i }).click();
  const stepUpInput = page.locator('input[name="mfa-backup-stepup-code"]');
  const codesArea = page.getByRole("textbox", { name: "Backup codes" });
  await expect(stepUpInput.or(codesArea).first()).toBeVisible();
  if (await stepUpInput.isVisible()) {
    await stepUpInput.fill(await codeWithRunway(page));
    await page.getByRole("button", { name: /^verify$/i }).click();
    await expect(codesArea).toBeVisible();
  }

  // Codes render: 10 single-use codes from the unambiguous alphabet.
  const codesText = await codesArea.inputValue();
  const codes = codesText.split("\n").filter(Boolean);
  expect(codes).toHaveLength(10);
  for (const c of codes) {
    expect(c).toMatch(/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{10}$/);
  }
  // "N unused backup codes remaining" reflects the fresh set.
  await expect(page.getByText(/10 unused backup codes remaining/i)).toBeVisible();

  // 5. Download exercises the hafi-backup-codes.txt export.
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /download \.txt/i }).click(),
  ]);
  expect(download.suggestedFilename()).toBe("hafi-backup-codes.txt");

  // Cleanup (disable MFA) runs in afterEach so it happens even if an assertion
  // above fails after MFA was enabled.
});
