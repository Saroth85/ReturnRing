/// <reference types="vitest/config" />
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';
import { returnringMock } from './mock/returnring-mock';
import pkg from './package.json' with { type: 'json' };

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../..');

// API mode (docs/ui-design.md 11):
// - mock: the browser adapter answers from contracts/examples/api through index.json. Default for
//   `vite` (development), `vitest` (test) and `--mode mock`. The examples are bundled only here.
// - live: fetch to /api, /healthz and /readyz on the same origin. Default for `vite build`
//   (production) and `--mode live`. In dev the server proxies to RETURNRING_API_URL
//   (http://127.0.0.1:8000 by default); RETURNRING_API_URL=mock serves the examples over HTTP
//   instead (mock/returnring-mock.ts), which exercises the live adapter end to end.
// VITE_API_MODE=mock|live overrides the default.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, here, '');
  const apiMode = env.VITE_API_MODE || (mode === 'production' || mode === 'live' ? 'live' : 'mock');
  if (apiMode !== 'mock' && apiMode !== 'live') {
    throw new Error(`VITE_API_MODE must be mock or live, not ${apiMode}`);
  }
  const apiTarget = env.RETURNRING_API_URL || 'http://127.0.0.1:8000';
  const mockServer = apiMode === 'live' && apiTarget === 'mock';
  const proxy =
    apiMode === 'live' && !mockServer
      ? Object.fromEntries(['/api', '/healthz', '/readyz'].map((p) => [p, { target: apiTarget, changeOrigin: true }]))
      : undefined;

  return {
    plugins: [react(), tailwindcss(), mockServer ? returnringMock({ repoRoot }) : null],
    define: {
      __API_MODE__: JSON.stringify(apiMode),
    },
    resolve: {
      alias: [
        { find: /^@\//, replacement: `${path.join(here, 'src')}/` },
        // The mock adapter (and with it every contract example) exists only in mock builds.
        {
          find: /^#mock-adapter$/,
          replacement: path.join(here, 'src/api/adapters', apiMode === 'mock' ? 'mock.ts' : 'mock.disabled.ts'),
        },
      ],
    },
    // Pre-bundle every runtime dependency at startup: a dependency found later (a lazily loaded
    // view) makes Vite optimise again and reload the page, which breaks a running E2E test.
    optimizeDeps: {
      include: [...Object.keys(pkg.dependencies), 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime'],
    },
    server: {
      // The contract examples live outside apps/ui.
      fs: { allow: [repoRoot] },
      proxy,
    },
    preview: { proxy },
    build: {
      sourcemap: true,
      chunkSizeWarningLimit: 900,
    },
    test: {
      environment: 'happy-dom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.mjs'],
      restoreMocks: true,
      testTimeout: 15000,
    },
  };
});
