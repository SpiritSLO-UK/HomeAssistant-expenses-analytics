import { defineConfig, devices } from "@playwright/test";

// The suite runs against a running instance of the app (the standalone build,
// which serves the SPA and the API from the same origin). Point it at the demo
// on http://127.0.0.1:8099 by default; CI sets E2E_BASE_URL to the container it
// boots. Standalone runs as a local owner with no login, so tests can hit pages
// directly with no auth step.
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8099";

export default defineConfig({
  testDir: "./tests",
  // The app is a single instance backed by one SQLite database with a bounded
  // connection pool (size 5 + overflow 10 = 15). Parallel browser workers all
  // share that one backend, and a single dashboard load already fans out ~10
  // concurrent card queries, so running specs in parallel exhausts the pool
  // (QueuePool timeout -> 500). Run serially: correct for a single-instance app
  // and keeps the report deterministic.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // HTML report is the deliverable ("provide me with a report as well"); list
  // gives readable console output while running. JUnit for CI test summaries.
  reporter: [
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["list"],
    ["junit", { outputFile: "playwright-report/junit.xml" }],
  ],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
