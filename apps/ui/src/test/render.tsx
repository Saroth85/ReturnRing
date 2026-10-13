// Render helpers: a fresh query client per test, and the whole app at a route (memory router
// over the real route table, the mock adapter answering from the contract examples).
import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router';
import { createQueryClient } from '@/api/queries';
import { buildRoutes } from '@/app/router';

export function renderWithProviders(ui: ReactElement, { route = '/' }: { route?: string } = {}) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export function renderApp(route: string) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
  const router = createMemoryRouter(buildRoutes(), { initialEntries: [route] });
  const result = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, client };
}
