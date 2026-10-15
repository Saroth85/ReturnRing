/// <reference types="vite/client" />

/** VITE_API_MODE of the build, set by vite.config.ts. */
declare const __API_MODE__: 'mock' | 'live';
/** Whether a mock answers this build (the mock adapter or the dev server's HTTP mock): vite.config.ts. */
declare const __MOCK_SCENARIOS__: boolean;

interface ImportMetaEnv {
  /** `true` builds the public read-only demo (docs/ui-design.md 3.1). */
  readonly VITE_READ_ONLY?: string;
  /** Origin of the cached demo, for the failover of /demo (docs/ui-design.md 9.5). */
  readonly VITE_CACHED_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
