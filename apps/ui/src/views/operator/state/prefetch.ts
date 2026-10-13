// Prefetching case cards (docs/ui-design.md 6.1, 6.2.1): the top rows of the queue, a hovered
// row and the next two cases of the `?from` view, so the operator loop stays under the US-301
// budget. Same key and freshness as useCase. (A shared prefetchCase in src/api/queries.ts would
// replace this: open issue.)
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { api } from '@/api/client';
import { queryKeys } from '@/api/queries';

const CASE_STALE_MS = 60_000;

export function usePrefetchCase(): (returnId: string) => void {
  const client = useQueryClient();
  return useCallback(
    (returnId: string) => {
      void client.prefetchQuery({
        queryKey: queryKeys.case(returnId),
        queryFn: ({ signal }) => api.getCase(returnId, { signal }),
        staleTime: CASE_STALE_MS,
      });
    },
    [client],
  );
}
