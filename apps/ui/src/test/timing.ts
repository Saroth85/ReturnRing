// Test timing (UI39): one ceiling for the async queries of Testing Library, below the per-test
// timeout of vite.config.ts (test.testTimeout), so a slow wait fails with its own message.
export const ASYNC_WAIT_MS = 20_000;
