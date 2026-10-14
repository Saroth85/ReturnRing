import clsx from 'clsx';
import { API_MODE } from '@/api/adapters';
import { useApiMeta } from '@/api/meta';
import { useReadyz } from '@/api/queries';
import { MODE } from '@/copy/common';

/**
 * The language model's state from the `vllm` check of /readyz, or a recent 503 (5.2). It never
 * blocks decisions: rules decide, the model only drafts text.
 */
export function LlmStatusPill() {
  const { data } = useReadyz();
  const meta = useApiMeta();
  if (!data) return null;
  const vllm = data.checks.find((c) => c.component === 'vllm');
  const state =
    meta.llmUnavailableAt !== null || vllm?.status === 'unavailable' ? 'unavailable' : vllm?.status === 'ok' ? 'ready' : 'off';
  // On the mock adapter the vllm check is a contract example, not a model that answers (UI12); a
  // 503 fixture still shows as unavailable.
  if (API_MODE === 'mock' && state !== 'unavailable') {
    return (
      <span data-llm-status="contract" className="hatch-cached inline-flex items-center rounded-full border border-cached-stripe px-2 py-0.5 text-caption">
        {MODE.llmContract}
      </span>
    );
  }
  const text = state === 'ready' ? MODE.llmReady : state === 'off' ? MODE.llmOff : MODE.llmUnavailable;
  return (
    <span
      data-llm-status={state}
      className={clsx(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-caption',
        state === 'unavailable' ? 'border-danger-fg text-danger-fg' : 'border-border text-ink-2',
      )}
    >
      {text}
    </span>
  );
}
