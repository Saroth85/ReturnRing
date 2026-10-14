import { mockAdapter } from '#mock-adapter';
import type { ApiAdapter } from '../transport';
import { liveAdapter } from './live';

/** VITE_API_MODE of the build (vite.config.ts): mock serves the contract examples. */
export const API_MODE: 'mock' | 'live' = __API_MODE__;

function pick(): ApiAdapter {
  if (API_MODE === 'mock') {
    if (!mockAdapter) throw new Error('VITE_API_MODE=mock but the mock adapter is not in this build');
    return mockAdapter;
  }
  return liveAdapter;
}

export const adapter: ApiAdapter = pick();
