// Playwright runs against a bundled mock build (contract examples, no API needed) served by
// `vite preview`: every page is a prebuilt chunk, so no test waits on the dev server transforming
// a lazy route on demand, which made first renders take more than 5 s on a loaded machine (UI39).
// UI_E2E_SERVER=dev runs against the Vite dev server instead, for quick local iteration.
// Chromium comes from PLAYWRIGHT_BROWSERS_PATH (preinstalled build 1194, the one @playwright/test
// 1.56.1 expects): never run `playwright install` here.
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.UI_E2E_PORT ?? 5199);
const DEV_SERVER = process.env.UI_E2E_SERVER === 'dev';
const HOST = `--port ${PORT} --strictPort --host 127.0.0.1`;

export default defineConfig({
  testDir: './tests/e2e',
  // The live specs need a running API: playwright.live.config.ts (npm run test:e2e:live).
  testIgnore: ['**/live/**'],
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list']],
  timeout: 60_000,
  // One ceiling for every assertion on a loaded machine; each returns as soon as it holds (UI39).
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: DEV_SERVER ? `npx vite ${HOST}` : `npm run build:mock && npx vite preview --outDir dist-mock ${HOST}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    env: { VITE_API_MODE: 'mock' },
  },
});
