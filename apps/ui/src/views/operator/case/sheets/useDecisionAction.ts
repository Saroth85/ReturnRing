// Submitting a decision action (docs/ui-design.md 6.3.3): never optimistic. The answer must name
// the return it was sent for; an answer about another return (a mock fallback, or a server bug)
// is reported, never applied.
import { useState } from 'react';
import type { CaseCard, DecisionResult } from '@/api/models';
import { useDecide } from '@/api/mutations';
import { OPERATOR } from '../../copy';
import type { DecisionBody } from '../../state/requests';

export function useDecisionAction(card: CaseCard, onDone: (result: DecisionResult) => void) {
  const decide = useDecide(card.return_id);
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
    });
  };
  const reset = () => {
    decide.reset();
    setMismatch(null);
  };
  return { submit, pending: decide.isPending, error: decide.isError ? decide.error : null, mismatch, reset };
}
