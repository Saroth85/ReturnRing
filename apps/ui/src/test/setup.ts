import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';
import { ASYNC_WAIT_MS } from './timing';

// findBy* and waitFor return as soon as their condition holds; the ceiling only matters on a loaded
// machine, where a first render of a whole page can take seconds (UI39). One ceiling for every
// test instead of per-call 5 s timeouts.
configure({ asyncUtilTimeout: ASYNC_WAIT_MS });

// React checks this flag before it warns about updates outside act(); Testing Library wraps its
// own calls, and the happy-dom environment does not set it.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  cleanup();
});
