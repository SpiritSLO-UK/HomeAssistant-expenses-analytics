import { type Page, expect } from "@playwright/test";

// The app is a HashRouter SPA, so routes are "/#/<path>". Standalone (the demo /
// CI target) runs as a local owner with no login, so navigation needs no auth.

export type PageDef = { route: string; heading: string; nav?: string };

// Every top-level page + the heading that proves it rendered. Headings captured
// from the running app; getByRole("heading", { name }) matches any level.
export const PAGES: PageDef[] = [
  { route: "/", heading: "Dashboard" },
  { route: "/search", heading: "Search" },
  { route: "/import", heading: "Import" },
  { route: "/transactions", heading: "Transactions" },
  { route: "/categories", heading: "Categories" },
  { route: "/vendors", heading: "Vendors" },
  { route: "/rules", heading: "Rules" },
  { route: "/projects", heading: "Projects" },
  { route: "/travel", heading: "Travel" },
  { route: "/business", heading: "Business expenses" },
  { route: "/budgets", heading: "Budgets" },
  { route: "/savings", heading: "Savings" },
  { route: "/investments", heading: "Investments & pensions" },
  { route: "/accounts", heading: "Accounts" },
  { route: "/assets", heading: "Cars & assets" },
  { route: "/energy", heading: "Energy cost offset" },
  { route: "/allowance", heading: "Allowance" },
  { route: "/subscriptions", heading: "Subscriptions" },
  { route: "/receipts", heading: "Receipts" },
  { route: "/review", heading: "Review Queue" },
  { route: "/users", heading: "Users & access" },
  { route: "/logs", heading: "Logs" },
  { route: "/settings", heading: "Settings" },
];

// Navigate to a hash route and wait for its heading to be visible. Avoids
// waitForLoadState("networkidle") - the app polls, so the network never idles.
export async function gotoPage(page: Page, def: PageDef): Promise<void> {
  await page.goto(`/#${def.route}`);
  await expect(
    page.getByRole("heading", { name: def.heading }).first(),
  ).toBeVisible();
}

// Attach console/page-error + HTTP-5xx collectors. JS exceptions are real bugs
// (hard fail); 5xx responses are reported so the run surfaces backend errors.
export function collectErrors(page: Page): { jsErrors: string[]; serverErrors: string[] } {
  const jsErrors: string[] = [];
  const serverErrors: string[] = [];
  page.on("pageerror", (e) => jsErrors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 500) serverErrors.push(`${r.status()} ${r.url()}`);
  });
  return { jsErrors, serverErrors };
}
