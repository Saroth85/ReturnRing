// The state of the LLM status pill (docs/ui-design.md 5.2), pure and tested.
import type { ReadyzResponse } from '@/api/models';

export type LlmPillState = 'contract' | 'ready' | 'mock' | 'cached' | 'off' | 'unavailable';

/**
 * The language model's state (5.2) from /readyz: the `vllm` check, llm_mode and mode, or a recent
 * 503. The mock language model drafts every text live (generation_path model, served_from live),
 * so it is "mock", never "off"; in cached mode the texts are precomputed or the template.
 */
export function llmPillState(
  readyz: Pick<ReadyzResponse, 'checks' | 'llm_mode' | 'mode'>,
  llmUnavailableRecently: boolean,
  apiMode: 'mock' | 'live',
): LlmPillState {
  const vllm = readyz.checks.find((c) => c.component === 'vllm');
  if (llmUnavailableRecently || vllm?.status === 'unavailable') return 'unavailable';
  // On the mock adapter the vllm check is a contract example, not a model that answers (UI12).
  if (apiMode === 'mock') return 'contract';
  if (vllm?.status === 'ok') return 'ready';
  if (readyz.mode === 'cached') return 'cached';
  if (readyz.llm_mode === 'mock') return 'mock';
  return 'off';
}
