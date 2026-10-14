import { MessageSquareText } from 'lucide-react';
import type { GenerationInfo } from '@/api/models';
import { MESSAGE } from '@/copy/common';

/**
 * How a generated text was produced (docs/ui-design.md 5.4): the validator path, plus the cached
 * and mock labels that keep live and cached apart (US-901).
 */
export function GenerationBadge({ generation }: { generation: GenerationInfo }) {
  const parts = [MESSAGE.generation[generation.generation_path]];
  if (generation.served_from === 'cached') parts.push(MESSAGE.generation.cached);
  if (generation.llm_mode === 'mock') parts.push(MESSAGE.generation.mock);
  return (
    <span
      data-constant
      className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-3"
      data-generation-path={generation.generation_path}
      data-served-from={generation.served_from}
    >
      <MessageSquareText aria-hidden="true" size={14} />
      {parts.join(' · ')}
    </span>
  );
}
