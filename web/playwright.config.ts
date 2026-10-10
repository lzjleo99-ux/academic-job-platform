import { defineConfig, devices } from "@playwright/test";

// T15 acceptance: "manual E2E script (documented, Playwright)". Run against a live stack:
//   python tools/dev_up.py           # API on :8000
//   npm run dev                      # web on :3000 (separate terminal)
//   npm run e2e
// Traces/screenshots land in test-results/ (see the reporting evidence in pm/outbox).
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  reporter: [["html", { outputFolder: "playwright-report", open: "never" }], ["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "on",
    screenshot: "on",
  },
  // This machine's Playwright-bundled Chromium build isn't available for its exact macOS point release
  // (mac13-arm64) - channel: 'chrome' uses the system-installed Google Chrome instead, needing no browser
  // download. If Chrome isn't installed elsewhere this is run, drop the channel line to use the bundled build.
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: "chrome" } }],
});
