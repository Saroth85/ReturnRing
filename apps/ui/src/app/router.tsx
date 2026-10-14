import { createBrowserRouter, type RouteObject } from 'react-router';
import { VIEW_ROUTES } from './composition';
import { NotFound, RootRedirect, RouteError } from './pages';
import { Shell } from './Shell';

/** Every route: `/` by persona, the views' routes inside the shell, then not found. */
export function buildRoutes(): RouteObject[] {
  // Dev-only routes (the agent harness) exist in dev and mock builds, never in a live build.
  const routes = VIEW_ROUTES.filter((r) => !r.devOnly || import.meta.env.DEV || __API_MODE__ === 'mock');
  return [
    {
      element: <Shell />,
      errorElement: <RouteError />,
      children: [
        { index: true, element: <RootRedirect />, handle: { title: '' } },
        ...routes.map(
          (r): RouteObject => ({
            path: r.path,
            element: <r.Component />,
            errorElement: <RouteError />,
            handle: { title: r.title },
          }),
        ),
        { path: '*', element: <NotFound />, handle: { title: 'Not found' } },
      ],
    },
  ];
}

export const createAppRouter = () => createBrowserRouter(buildRoutes());
