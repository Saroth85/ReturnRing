// Playwright against the live API (docs/ui-design.md 14.2; `npm run test:e2e:live`). Not in CI:
// CI has no API server. The UI is the production bundle (`npm run build`, live adapter) served by
// `vite preview`, which proxies /api, /healthz and /readyz to RETURNRING_API_URL; the specs read
// the live ids from GET /api/demo/cases, never from the contract examples.
//
//   RETURNRING_API_URL=http://127.0.0.1:8000 npm run test:e2e:live
//
// The global setup checks the API first: /readyz answers with the contract's major.minor version
// and a demo reset has stored the demo cases (else: run python -m apps.api.reset). UI_E2E_RESET_CMD
// runs a reset before the suite. The walk writes (it approves cases): run it after a fresh reset,
// against a writable API; the read-only spec runs only when /readyz says public_read_only.
// Chromium comes from PLAYWRIGHT_BROWSERS_PATH: never run `playwright install` here.
import { defineConfig, devices } from '@playwright/test';

// The same default as the dev proxy (vite.config.ts) and tests/e2e/live/env.ts. Not imported from
// tests/: the Docker build context has no tests/ and `npm run build` typechecks this file.
const API_URL = (process.env.RETURNRING_API_URL ?? 'http://127.0.0.1:8000').replace(/\/+$/, '');

const PORT = Number(process.env.UI_E2E_PORT ?? 5198);
const HOST = `--port ${PORT} --strictPort --host 127.0.0.1`;

export default defineConfig({
  testDir: './tests/e2e/live',
  testMatch: /.*\.live\.spec\.ts$/,
  globalSetup: './tests/e2e/live/global-setup.ts',
  // One API, one store: the walk changes it, so the specs run one at a time, in file order.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list']],
  // The live API on a CPU decides, recomputes and streams in seconds, not milliseconds.
  timeout: 180_000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npm run build && npx vite preview ${HOST}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    env: { RETURNRING_API_URL: API_URL, VITE_API_MODE: 'live' },
  },
});
