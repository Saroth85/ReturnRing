import { MessageSquareText } from 'lucide-react';
import { useApiMeta } from '@/api/meta';
import type { GenerationInfo } from '@/api/models';
import { MESSAGE } from '@/copy/common';

/**
 * How a generated text was produced (docs/ui-design.md 5.4): the path of llm.generate, why it fell
 * back to the template, the model calls, plus the cached and mock labels that keep live and
 * cached apart (US-901). Where cached outputs were made is readyz.label's claim, not this badge's:
 * a cached text carries that label as /readyz gives it (MI300X or rehearsal), never a fixed GPU
 * name, and a neutral word until /readyz answers (ADR-15 sections 16, 17).
 */
export function GenerationBadge({ generation }: { generation: GenerationInfo }) {
  const { cachedLabel } = useApiMeta();
  const parts = [MESSAGE.generation[generation.generation_path]];
  if (generation.fallback_reason !== null) parts.push(MESSAGE.generation.fallback[generation.fallback_reason]);
  parts.push(MESSAGE.generation.modelCalls(generation.model_calls));
  if (generation.served_from === 'cached') parts.push(cachedLabel ?? MESSAGE.generation.cached);
  if (generation.llm_mode === 'mock') parts.push(MESSAGE.generation.mock);
  return (
    <span
      data-constant
      className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-3"
      data-generation-path={generation.generation_path}
      data-served-from={generation.served_from}
      data-fallback-reason={generation.fallback_reason ?? undefined}
    >
      <MessageSquareText aria-hidden="true" size={14} />
      {parts.join(' · ')}
    </span>
  );
}
