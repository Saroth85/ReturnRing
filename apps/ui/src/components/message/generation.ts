// Which model wrote a text, from the generation record itself (rules 11 and 13): the served model
// id (generation.model), "mock language model" when llm_mode is mock, and no model at all for the
// reviewed template. Never a fixed model name: a mock text, or a cached text another model made
// (ADR-15 section 17), must not be attributed to the model the project plans to serve.
import type { GenerationInfo } from '@/api/models';
import { MESSAGE } from '@/copy/common';

export type GenerationOrigin = Pick<GenerationInfo, 'generation_path' | 'llm_mode' | 'model'>;

/** The model that wrote the text, or null for the reviewed template (no model text). */
export function generationModel(g: GenerationOrigin): string | null {
  if (g.generation_path === 'template') return null;
  return g.llm_mode === 'mock' ? MESSAGE.generation.mockModel : g.model;
}

/** "google/gemma-4-31b-it · validated on the first call", "Mock language model · …", or the template. */
export function generationLabel(g: GenerationOrigin): string {
  const t = MESSAGE.generation;
  const model = generationModel(g);
  if (model === null) return t.template;
  return g.generation_path === 'regenerated' ? t.secondCall(model) : t.firstCall(model);
}
