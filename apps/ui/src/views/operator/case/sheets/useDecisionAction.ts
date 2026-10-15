// Submitting a decision action (docs/ui-design.md 6.3.3): never optimistic. The answer must name
// the return it was sent for; an answer about another return (a mock fallback, or a server bug)
// is reported, never applied. A 409 conflict (already decided, a stale proposal the service has
// just decided again, a refund past its deadline, a renewal before the review date) loads the
// case again at once, so the card shows the state the service refused the act in; the sheet shows
// the service's message verbatim.
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { isApiError } from '@/api/errors';
import type { CaseCard, DecisionResult } from '@/api/models';
import { useDecide } from '@/api/mutations';
import { queryKeys } from '@/api/queries';
import { OPERATOR } from '../../copy';
import type { DecisionBody } from '../../state/requests';

export function useDecisionAction(card: CaseCard, onDone: (result: DecisionResult) => void) {
  const decide = useDecide(card.return_id);
  const client = useQueryClient();
  const [mismatch, setMismatch] = useState<string | null>(null);
  const submit = (body: DecisionBody) => {
    setMismatch(null);
    decide.mutate(body, {
      onSuccess: (result) => {
        if (result.decision.return_id !== card.return_id) {
          setMismatch(OPERATOR.sheets.mismatch(result.decision.return_id, card.return_id));
          return;
        }
        onDone(result);
      },
      onError: (error) => {
        if (!isApiError(error) || error.code !== 'conflict') return;
        void client.invalidateQueries({ queryKey: queryKeys.case(card.return_id) });
        void client.invalidateQueries({ queryKey: ['queue'] });
        void client.invalidateQueries({ queryKey: ['queueCount'] });
      },
    });
  };
  const reset = () => {
    decide.reset();
    setMismatch(null);
  };
  return { submit, pending: decide.isPending, error: decide.isError ? decide.error : null, mismatch, reset };
}
