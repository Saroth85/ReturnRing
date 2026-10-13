// Write hooks over the typed client (docs/ui-design.md 11.2 and 13.3 item 6). Decisions are never
// optimistic: the UI waits for the server's answer, merges it into the cache, then invalidates
// what it may have changed. Every action sends the decision_id it was shown (ADR-15).
import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { api, type BodyOf } from './client';
import type { CaseCard, DecisionView } from './models';
import { queryKeys } from './queries';

/** Never downgrade: a decision with a lower version than the cached one is ignored (11.2). */
export function mergeDecision(client: QueryClient, returnId: string, decision: DecisionView): void {
  client.setQueryData<CaseCard>(queryKeys.case(returnId), (card) => {
    // An answer about another return (the mock serves the nearest example) is never written here.
    if (!card || card.return_id !== decision.return_id) return card;
    if (card.decision.decision_id !== decision.decision_id && card.decision.version > decision.version) return card;
    return { ...card, decision };
  });
}

function invalidateCaseWork(client: QueryClient, returnId: string): Promise<unknown> {
  return Promise.all([
    client.invalidateQueries({ queryKey: queryKeys.case(returnId) }),
    client.invalidateQueries({ queryKey: ['queue'] }),
    client.invalidateQueries({ queryKey: ['queueCount'] }),
    client.invalidateQueries({ queryKey: ['audit'] }),
    client.invalidateQueries({ queryKey: ['outbox'] }),
  ]);
}

export function useDecide(returnId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/returns/{return_id}/decision'>) => api.decide(returnId, body),
    onSuccess: (result) => {
      mergeDecision(client, returnId, result.decision);
      return invalidateCaseWork(client, returnId);
    },
  });
}

export function useRegenerate(returnId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/returns/{return_id}/regenerate'>) => api.regenerate(returnId, body),
    onSuccess: (result) => {
      client.setQueryData<CaseCard>(queryKeys.case(returnId), (card) =>
        card && card.decision.decision_id === result.decision_id ? { ...card, draft: result.draft, draft_status: 'ready' } : card,
      );
      return client.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

export function useAddEvidence(returnId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/returns/{return_id}/evidence'>) => api.addEvidence(returnId, body),
    onSuccess: (result) => {
      mergeDecision(client, returnId, result.decision);
      return invalidateCaseWork(client, returnId);
    },
  });
}

export function useDisputeLink(returnId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ linkId, body }: { linkId: string; body: BodyOf<'POST /api/links/{link_id}/dispute'> }) =>
      api.disputeLink(linkId, body),
    onSuccess: async (result) => {
      if (returnId && result.recompute) mergeDecision(client, returnId, result.recompute.decision);
      await Promise.all(result.affected_return_ids.map((id) => invalidateCaseWork(client, id)));
    },
  });
}

export const useCheckoutEvaluate = () =>
  useMutation({ mutationFn: (body: BodyOf<'POST /api/checkout/evaluate'>) => api.checkoutEvaluate(body) });

export function useCheckoutConfirm() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/checkout/confirm'>) => api.checkoutConfirm(body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['audit'] }),
  });
}

export const useCreateInspection = () =>
  useMutation({ mutationFn: (body: BodyOf<'POST /api/inspections'>) => api.createInspection(body) });

export function useConfirmInspection(inspectionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/inspections/{inspection_id}/confirm'>) => api.confirmInspection(inspectionId, body),
    onSuccess: (result) => invalidateCaseWork(client, result.decision.return_id),
  });
}

export function useUpdateAssumptions() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'PUT /api/simulator/assumptions'>) => api.updateAssumptions(body),
    onSuccess: (result) => {
      client.setQueryData(queryKeys.assumptions, result);
      return Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.matrix }),
        client.invalidateQueries({ queryKey: ['audit'] }),
      ]);
    },
  });
}

export function useCreateCatalogTask() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/catalog-tasks'>) => api.createCatalogTask(body),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.tasks }),
        client.invalidateQueries({ queryKey: ['anomalies'] }),
      ]),
  });
}

export function useUpdateCatalogTask(taskId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'PATCH /api/catalog-tasks/{task_id}'>) => api.updateCatalogTask(taskId, body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.tasks }),
  });
}

export function useCreateCouponRule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/coupon-rules'>) => api.createCouponRule(body),
    onSuccess: (result) => client.setQueryData(queryKeys.couponRules, result),
  });
}

export function useReverseCouponApplication(applicationId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BodyOf<'POST /api/coupon-rules/applications/{application_id}/reverse'>) =>
      api.reverseCouponApplication(applicationId, body),
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.couponRules }),
        client.invalidateQueries({ queryKey: ['case'] }),
        client.invalidateQueries({ queryKey: ['audit'] }),
      ]),
  });
}
