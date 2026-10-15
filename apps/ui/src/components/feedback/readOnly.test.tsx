// The read-only public demo (ADR-15 section 15): the service decides (readyz.public_read_only), the
// build flag only until it answers. Every gate and the 403 notice follow it.
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { createQueryClient } from '@/api/queries';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { FEEDBACK } from '@/copy/common';
import { ForbiddenRoleNotice } from './notices';
import { RoleGate } from './RoleGate';
import { isReadOnlyRefusal, READ_ONLY, useReadOnly, useRoleGate } from './roles';

function wrapper({ children }: { children: ReactNode }) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() =>
  act(() => {
    setScenario('default');
    setPersona('sara');
  }),
);

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

describe('useRoleGate and RoleGate follow /readyz, not only the build flag', () => {
  it('allows the operator to decide until /readyz says read-only', async () => {
    const { result } = renderHook(() => useRoleGate('POST /api/returns/{return_id}/decision'), { wrapper });
    await waitFor(() => expect(result.current).toEqual({ allowed: true, reason: null }));
  });

  it('disables every write on the read-only demo with the reason, and keeps the agent and the evaluation', async () => {
    act(() => setScenario('read_only'));
    const decide = renderHook(() => useRoleGate('POST /api/returns/{return_id}/decision'), { wrapper });
    await waitFor(() => expect(decide.result.current).toEqual({ allowed: false, reason: FEEDBACK.readOnlyDemo }));
    const ask = renderHook(() => useRoleGate('POST /api/agent/ask'), { wrapper });
    await waitFor(() => expect(ask.result.current.allowed).toBe(true));
    const evaluate = renderHook(() => useRoleGate('POST /api/checkout/evaluate'), { wrapper });
    await waitFor(() => expect(evaluate.result.current.allowed).toBe(true));
  });

  it('renders the control disabled with the reason under it, never hidden', async () => {
    act(() => setScenario('read_only'));
    render(
      <RoleGate action="POST /api/checkout/confirm">
        {(gate) => (
          <button type="button" disabled={!gate.allowed}>
            Place order
          </button>
        )}
      </RoleGate>,
      { wrapper },
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Place order' })).toBeDisabled());
    expect(document.querySelector('[data-role-reason]')?.textContent).toBe(FEEDBACK.readOnlyDemo);
  });
});

describe('ForbiddenRoleNotice', () => {
  it('tells the read-only refusal from a role refusal', () => {
    expect(isReadOnlyRefusal(false, 'operator', 'POST /api/returns/{return_id}/decision')).toBe(false);
    expect(isReadOnlyRefusal(true, 'operator', 'POST /api/returns/{return_id}/decision')).toBe(true);
    expect(isReadOnlyRefusal(true, 'warehouse', 'POST /api/agent/ask')).toBe(false);
    expect(isReadOnlyRefusal(true, 'operator')).toBe(true);
  });

  it('on the read-only demo, quotes the API and offers no persona switch', async () => {
    act(() => setScenario('read_only'));
    const message = 'the public demo is read-only (PUBLIC_READ_ONLY)';
    const operator = 'operator';
    render(<ForbiddenRoleNotice role={operator} action="approve" endpoint="POST /api/returns/{return_id}/decision" message={message} />, {
      wrapper,
    });
    const notice = await waitFor(() => {
      const found = document.querySelector('[data-read-only-refusal]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(notice.textContent).toContain(FEEDBACK.readOnlyDemo);
    expect(notice.querySelector('[data-verbatim]')?.textContent).toBe(message);
    expect(screen.queryByRole('button', { name: /Acting as/ })).toBeNull();
  });

  it('a role the agent refuses stays a role notice on the read-only demo', async () => {
    act(() => {
      setScenario('read_only');
      setPersona('ahmed');
    });
    const warehouse = 'warehouse';
    render(<ForbiddenRoleNotice role={warehouse} action="ask the assistant" endpoint="POST /api/agent/ask" message="role" />, { wrapper });
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Acting as/ }).length).toBeGreaterThan(0));
    expect(document.querySelector('[data-read-only-refusal]')).toBeNull();
  });
});
