// The assistant's model as /readyz names it (rules 11, 13): never a fixed name over a mock model.
import { describe, expect, it } from 'vitest';
import { LLM_NAME } from '@/copy/constants';
import { AGENT } from './copy';
import { assistantModel } from './model';

describe('assistantModel', () => {
  it('identifies the AMD shared provider', () => {
    expect(assistantModel({ mode: 'live', llm_mode: 'tokenfactory' }, 'live')).toBe(AGENT.model.tokenfactory);
  });
  it('names the mock language model when the service runs it', () => {
    expect(assistantModel({ mode: 'live', llm_mode: 'mock' }, 'live')).toBe(AGENT.model.mock);
    expect(AGENT.model.mock).not.toContain(LLM_NAME);
  });

  it('names the model on vLLM only when the service is live on vLLM', () => {
    expect(assistantModel({ mode: 'live', llm_mode: 'vllm' }, 'live')).toBe(AGENT.model.vllm);
  });

  it('names no model in cached mode, nor before /readyz answers', () => {
    expect(assistantModel({ mode: 'cached', llm_mode: 'vllm' }, 'live')).toBeNull();
    expect(assistantModel(undefined, 'live')).toBeNull();
  });

  it('says contract examples on the mock adapter', () => {
    expect(assistantModel({ mode: 'live', llm_mode: 'vllm' }, 'mock')).toBe(AGENT.model.contract);
  });
});
