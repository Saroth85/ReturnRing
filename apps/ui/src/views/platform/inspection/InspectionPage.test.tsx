// Every matching parcel remains reachable through the API's bounded queue pages.
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

function pagedReturns() {
  const example = readExample<QueueResponse>('queue_response.json');
  const items = Array.from({ length: 26 }, (_, i) => ({ ...example.items[0]!, return_id: `RET-INSPECT-${i + 1}` }));
  return vi.spyOn(api, 'listReturns').mockImplementation(async (query) => {
    const page = query?.page ?? 1;
    const pageSize = query?.page_size ?? 25;
    return { ...example, items: items.slice((page - 1) * pageSize, page * pageSize), page, page_size: pageSize, total: items.length };
  });
}

describe('inspection return list', () => {
  it('uses the first page when a shared URL exceeds the API page bound', async () => {
    const list = pagedReturns();
    renderApp('/inspection?page=100001');
    await waitFor(() => expect(document.querySelectorAll('[data-inspection-row]')).toHaveLength(25));
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ path: ['withdrawal'], page: 1 }), expect.anything());
  });

  it('reaches parcels beyond the first page and keeps the search while paging', async () => {
    const list = pagedReturns();
    const { router } = renderApp('/inspection?q=parcel');
    const pager = await screen.findByRole('navigation', { name: 'Withdrawal-path return pages' });
    await waitFor(() => expect(within(pager).getByRole('button', { name: 'Next page' })).toBeEnabled());
    expect(document.querySelectorAll('[data-inspection-row]')).toHaveLength(25);
    expect(within(pager).getByRole('button', { name: 'Previous page' })).toBeDisabled();
    fireEvent.click(within(pager).getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(document.querySelector('[data-inspection-row="RET-INSPECT-26"]')).not.toBeNull());
    expect(router.state.location.search).toBe('?q=parcel&page=2');
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ path: ['withdrawal'], q: 'parcel', page: 2 }), expect.anything());
    expect(within(pager).getByRole('button', { name: 'Next page' })).toBeDisabled();
    fireEvent.click(within(pager).getByRole('button', { name: 'Previous page' }));
    await waitFor(() => expect(router.state.location.search).toBe('?q=parcel'));
    await waitFor(() => expect(document.querySelectorAll('[data-inspection-row]')).toHaveLength(25));
  });

  it('starts at the first page after changing the search', async () => {
    const list = pagedReturns();
    const { router } = renderApp('/inspection?q=parcel&page=2');
    await waitFor(() => expect(document.querySelector('[data-inspection-row="RET-INSPECT-26"]')).not.toBeNull());
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search by return, order or customer' }), { target: { value: 'another' } });
    await waitFor(() => expect(router.state.location.search).toBe('?q=another'));
    await waitFor(() => expect(list).toHaveBeenCalledWith(expect.objectContaining({ q: 'another', page: 1 }), expect.anything()));
  });
});
