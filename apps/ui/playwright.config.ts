// Playwright runs against the Vite dev server in mock mode (contract examples, no API needed).
// Chromium comes from PLAYWRIGHT_BROWSERS_PATH (preinstalled build 1194, the one @playwright/test
// 1.56.1 expects): never run `playwright install` here.
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.UI_E2E_PORT ?? 5199);

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list']],
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { VITE_API_MODE: 'mock' },
  },
});
