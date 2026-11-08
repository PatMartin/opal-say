import { defineConfig } from '@playwright/test'

// E2E tests run against a real browser + the Vite dev server. API responses are
// intercepted at the network layer (see e2e/app.spec.ts), so no live service is
// required. Point VITE_OPAL_SAY_API_URL at a real service for live checks.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://127.0.0.1:5199/ui/',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --port 5199 --strictPort',
    url: 'http://127.0.0.1:5199/ui/',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
