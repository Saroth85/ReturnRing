// Which language model the assistant runs on, as the service says (GET /readyz), never a fixed
// name (rules 11 and 13): the mock language model when llm_mode is mock, the served model on vLLM
// when the service is live with vLLM, contract examples on the mock adapter, and no name in cached
// mode (the assistant is not served there). Each answer's badge then names the model of its own
// generation record.
import { API_MODE } from '@/api/adapters';
import type { ReadyzResponse } from '@/api/models';
import { useReadyz } from '@/api/queries';
import { AGENT } from './copy';

export function assistantModel(readyz: Pick<ReadyzResponse, 'mode' | 'llm_mode'> | undefined, apiMode: 'mock' | 'live'): string | null {
  if (apiMode === 'mock') return AGENT.model.contract;
  if (!readyz || readyz.mode === 'cached') return null;
  return readyz.llm_mode === 'mock' ? AGENT.model.mock : AGENT.model.vllm;
}

export function useAssistantModel(): string | null {
  const { data } = useReadyz();
  return assistantModel(data, API_MODE);
}
