import { createBrowserRouter, type RouteObject } from 'react-router';
import { VIEW_ROUTES } from './composition';
import { NotFound, RootRedirect, RouteError } from './pages';
import { Shell } from './Shell';

/** Every route: `/` by persona, the views' routes inside the shell, then not found. */
export function buildRoutes(): RouteObject[] {
  const routes = VIEW_ROUTES.filter((r) => !r.devOnly || import.meta.env.DEV);
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
