// useReadOnly: the service decides (readyz.public_read_only); the build flag only until it answers.
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { createQueryClient } from '@/api/queries';
import { setScenario } from '@/api/scenario';
import { READ_ONLY, useReadOnly } from './roles';

function wrapper({ children }: { children: ReactNode }) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => act(() => setScenario('default')));

describe('useReadOnly', () => {
  it('falls back to the build flag, then follows /readyz', async () => {
    const { result } = renderHook(() => useReadOnly(), { wrapper });
    expect(result.current).toBe(READ_ONLY);
    await waitFor(() => expect(result.current).toBe(false));
  });

  it.each(['read_only', 'cached'])('is read-only when /readyz says so (%s)', async (scenario) => {
    act(() => setScenario(scenario));
    const { result } = renderHook(() => useReadOnly(), { wrapper });
    await waitFor(() => expect(result.current).toBe(true));
  });
});
