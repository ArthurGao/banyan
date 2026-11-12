import { defineConfig, devices } from '@playwright/test'

// The main flow runs against the real backend with fakes (e2e/serve.sh), which serves the production
// build on 8812. The three viewports are the ones every screen is checked at (frontend/CLAUDE.md).
const base = 'http://127.0.0.1:8812'

export default defineConfig({
  testDir: './e2e',
  // One backend and one database schema: tests run one at a time.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: base, trace: 'retain-on-failure' },
  projects: [
    { name: 'phone', use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: 'tablet-portrait', use: { ...devices['Desktop Chrome'], viewport: { width: 820, height: 1180 }, hasTouch: true } },
    { name: 'tablet-landscape', use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 1024 } } },
  ],
  webServer: { command: './e2e/serve.sh', url: `${base}/health`, reuseExistingServer: false, timeout: 180_000 },
})
