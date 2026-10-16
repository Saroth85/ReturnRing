// A queue row stops being new once opened, regardless of its navigation path.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { QueueResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';

afterEach(() => {
  vi.restoreAllMocks();
  act(() => {
    setPersona('sara');
    setScenario('default');
  });
});

describe('new queue rows', () => {
  it.each(['row click', 'return link', 'keyboard'] as const)('remembers a case opened by %s without duplicate navigation', async (method) => {
    const example = readExample<QueueResponse>('queue_response.json');
    const index = ['row click', 'return link', 'keyboard'].indexOf(method) + 1;
    const added = example.items[index]!;
    let page = { ...example, items: example.items.slice(0, 1), total: 1 };
    vi.spyOn(api, 'listReturns').mockImplementation(async () => page);
    const { router, client } = renderApp('/queue?tab=all');
    await screen.findByRole('link', { name: example.items[0]!.return_id });
    expect(screen.queryByText('New')).not.toBeInTheDocument();

    page = { ...page, items: [added, ...page.items], total: 2 };
    await act(async () => {
      client.setQueriesData({ queryKey: ['queue'] }, page);
    });
    const row = await waitFor(() => {
      const found = document.querySelector(`[data-return-row="${added.return_id}"]`) as HTMLElement;
      expect(found).not.toBeNull();
      expect(within(found).getByText('New')).toBeInTheDocument();
      return found;
    });
    if (method === 'return link') fireEvent.click(within(row).getByRole('link', { name: added.return_id }));
    else if (method === 'row click') fireEvent.click(row);
    else fireEvent.keyDown(window, { key: 'Enter' });

    await waitFor(() => expect(router.state.location.pathname).toBe(`/returns/${added.return_id}`));
    await act(() => router.navigate(-1));
    await waitFor(() => expect(router.state.location.pathname).toBe('/queue'));
    const reopened = await screen.findByRole('link', { name: added.return_id });
    expect(within(reopened.closest('tr')!).queryByText('New')).not.toBeInTheDocument();
  });
});
