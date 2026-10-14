// Prefetching case cards (docs/ui-design.md 6.1, 6.2.1): the top rows of the queue, a hovered
// row and the next two cases of the `?from` view, so the operator loop stays under the US-301
// budget. Same key and freshness as useCase (prefetchCase in src/api/queries.ts).
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { prefetchCase } from '@/api/queries';

export function usePrefetchCase(): (returnId: string) => void {
  const client = useQueryClient();
  return useCallback((returnId: string) => void prefetchCase(client, returnId), [client]);
}
