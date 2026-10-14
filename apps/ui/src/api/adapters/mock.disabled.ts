import type { ApiAdapter } from '../transport';

// Live builds alias #mock-adapter to this file (vite.config.ts), so no contract example and no
// mock code reaches the production bundle.
export const mockAdapter: ApiAdapter | null = null;
