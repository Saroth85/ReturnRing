// Every lazily loaded page module, imported once before the tests of a file that renders the whole
// app (UI39). React.lazy then resolves in a microtask, so no test's first render waits on Vite
// transforming a page on demand, which took more than 5 s on a loaded machine.
import { beforeAll } from 'vitest';

const pages = import.meta.glob(['../views/**/*Page.tsx', '../views/agent/dev/AgentHarness.tsx']);

export function preloadPages(): Promise<unknown[]> {
  return Promise.all(Object.values(pages).map((load) => load()));
}

/** Registers the preload as a beforeAll of the importing test file. */
export function preloadPagesBeforeAll(): void {
  beforeAll(() => preloadPages(), 120_000);
}
