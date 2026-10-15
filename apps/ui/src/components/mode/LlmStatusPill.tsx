import clsx from 'clsx';
import { API_MODE } from '@/api/adapters';
import { useApiMeta } from '@/api/meta';
import { useReadyz } from '@/api/queries';
import { MODE } from '@/copy/common';
import { llmPillState, type LlmPillState } from './llmState';

const TEXT: Record<LlmPillState, string> = {
  contract: MODE.llmContract,
  ready: MODE.llmReady,
  mock: MODE.llmMock,
  cached: MODE.llmCached,
  off: MODE.llmOff,
  unavailable: MODE.llmUnavailable,
};

/** It never blocks decisions: rules decide, the model only drafts text. */
export function LlmStatusPill() {
  const { data } = useReadyz();
  const meta = useApiMeta();
  if (!data) return null;
  const state = llmPillState(data, meta.llmUnavailableAt !== null, API_MODE);
  return (
    <span
      data-llm-status={state}
      className={clsx(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-caption',
        state === 'unavailable' && 'border-danger-fg text-danger-fg',
        (state === 'contract' || state === 'cached') && 'hatch-cached border-cached-stripe',
        (state === 'ready' || state === 'mock' || state === 'off') && 'border-border text-ink-2',
      )}
    >
      {TEXT[state]}
    </span>
  );
}
