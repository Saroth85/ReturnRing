import type { DecisionView } from '@/api/models';
import { useCaseUi } from '../context';

/** The decision id of a newer version than the one a sheet was opened on, or null (UI38). */
export function useNewerVersion(shown: Pick<DecisionView, 'decision_id'>): string | null {
  const { card } = useCaseUi();
  return card.decision.decision_id !== shown.decision_id ? card.decision.decision_id : null;
}
