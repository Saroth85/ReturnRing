// Mode banner (US-901, docs/ui-design.md 5.2): live and cached are never confused, and the banner
// is always there in cached mode.
import { QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { CONTRACT_VERSION } from '@/api/contract.gen';
import { metaStore, resetModesSeen } from '@/api/meta';
import type { ReadyzResponse } from '@/api/models';
import { createQueryClient, queryKeys } from '@/api/queries';
import { setScenario } from '@/api/scenario';
import { describeMode, ModeBanner, ModeCornerTag } from '@/components/mode';
import { readExample } from '@/test/examples';
import { renderWithProviders } from '@/test/render';

const live = readExample<ReadyzResponse>('readyz_response.live.json');
const cached = readExample<ReadyzResponse>('readyz_response.cached.json');
const noHeaders = { lastMode: null, modesSeen: { live: 0, cached: 0 } };
const base = { readyzFailed: false, meta: noHeaders, isMock: false, readOnly: false };

describe('describeMode: the rows of docs/ui-design.md 5.2', () => {
  it('live on the GPU', () => {
    const view = describeMode({ ...base, readyz: live });
    expect(view.kind).toBe('live');
    expect(`${view.title} · ${view.detail}`).toBe('LIVE · AMD Instinct MI300X · Gemma 4 on vLLM');
    expect(view.isCached).toBe(false);
  });

  it('live on CPU with the mock language model', () => {
    const view = describeMode({ ...base, readyz: { ...live, llm_mode: 'mock', device: 'cpu' } });
    expect(view.kind).toBe('live-cpu');
    expect(view.title).toBe('LIVE (CPU)');
    expect(view.detail).toContain('hand-written stand-ins');
  });

  it('cached, with the readyz label', () => {
    const view = describeMode({ ...base, readyz: cached });
    expect(view.kind).toBe('cached');
    expect(view.isCached).toBe(true);
    expect(`${view.title} · ${view.detail}`).toBe('CACHED DEMO · Precomputed on AMD MI300X · GPU off: nothing is computed live');
  });

  it('cached from the response header even before /readyz answers', () => {
    const view = describeMode({ ...base, readyz: undefined, meta: { lastMode: 'cached', modesSeen: { live: 0, cached: 1 } } });
    expect(view.kind).toBe('cached');
  });

  it('mixed when a header disagrees with /readyz', () => {
    const view = describeMode({ ...base, readyz: live, meta: { lastMode: 'cached', modesSeen: { live: 3, cached: 1 } } });
    expect(view.kind).toBe('mixed');
    expect(view.title).toBe('MIXED MODE: some responses are cached');
  });

  it('contract examples on the mock adapter', () => {
    const view = describeMode({ ...base, readyz: live, isMock: true });
    expect(view.kind).toBe('contract');
    expect(`${view.title} · ${view.detail}`).toBe('CONTRACT EXAMPLES · UI running on contracts/examples/api');
  });

  it('lists the failing checks when degraded, and the read-only demo', () => {
    const degraded: ReadyzResponse = {
      ...live,
      status: 'degraded',
      checks: [{ component: 'vllm', status: 'unavailable', detail: 'no answer' }, ...live.checks.slice(1)],
    };
    const view = describeMode({ ...base, readyz: degraded, readOnly: true });
    expect(view.degraded.map((c) => c.component)).toEqual(['vllm']);
    expect(view.suffix).toContain('read-only public demo');
  });

  it('says it is checking, or that /readyz did not answer', () => {
    expect(describeMode({ ...base, readyz: undefined }).kind).toBe('checking');
    expect(describeMode({ ...base, readyz: undefined, readyzFailed: true }).kind).toBe('unknown');
  });
});

describe('ModeBanner on the mock adapter', () => {
  afterEach(() => {
    // Unmount first: the stores below notify mounted components outside act().
    cleanup();
    setScenario('default');
    resetModesSeen();
  });

  it('shows the contract examples band, with no way to dismiss it', async () => {
    renderWithProviders(<ModeBanner />);
    const banner = await screen.findByRole('region', { name: 'Service mode' });
    await waitFor(() => expect(banner).toHaveAttribute('data-mode-banner', 'contract'));
    expect(within(banner).getByText('CONTRACT EXAMPLES')).toBeInTheDocument();
    expect(within(banner).queryByRole('button', { name: /close|dismiss|hide/i })).toBeNull();
  });

  it('is always visible in the cached scenario with the MI300X label and the corner tag', async () => {
    setScenario('cached');
    renderWithProviders(
      <>
        <ModeBanner />
        <ModeCornerTag />
      </>,
    );
    const banner = await screen.findByRole('region', { name: 'Service mode' });
    await waitFor(() => expect(banner.textContent).toContain('Precomputed on AMD MI300X'));
    expect(banner).toHaveAttribute('data-mode-banner', 'cached');
    expect(banner.textContent).toContain('CACHED DEMO');
    expect(banner.className).not.toContain('hidden');
    expect(document.querySelector('[data-mode-corner]')?.textContent).toMatch(/Cached demo/);
    expect(metaStore.get().lastMode).toBe('cached');
  });
});

describe('ContractVersionNotice (UI1)', () => {
  function renderWithReadyz(readyz: ReadyzResponse) {
    const client = createQueryClient();
    client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false, staleTime: Infinity } });
    client.setQueryData(queryKeys.readyz, readyz);
    return render(
      <QueryClientProvider client={client}>
        <ModeBanner />
      </QueryClientProvider>,
    );
  }

  it('shows a visible notice when the API serves another contract version', async () => {
    renderWithReadyz({ ...live, contract_version: '1.4.0' });
    const notice = await screen.findByRole('alert');
    expect(notice).toHaveAttribute('data-contract-mismatch', '1.4.0');
    expect(notice.textContent).toContain('1.4.0');
    expect(notice.textContent).toContain(CONTRACT_VERSION);
  });

  it('stays silent on the same version', async () => {
    renderWithReadyz({ ...live, contract_version: CONTRACT_VERSION });
    await screen.findByRole('region', { name: 'Service mode' });
    expect(document.querySelector('[data-contract-mismatch]')).toBeNull();
  });
});
