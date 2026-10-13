import type { GenerationInfo } from '@/api/models';
import { GenerationBadge } from '@/components/message/GenerationBadge';

interface GeneratedTextProps {
  generation: GenerationInfo;
  /** Plain text only: model output never renders as HTML or Markdown (T-NO-HTML). */
  children: string;
  lang?: 'it' | 'en';
}

/**
 * Model-written operator text (statements, agent answers), marked `data-generated` and shown
 * verbatim with how it was produced (docs/ui-design.md 5.1). It never changes the decision.
 */
export function GeneratedText({ generation, children, lang }: GeneratedTextProps) {
  return (
    <div data-generated data-verbatim className="space-y-1">
      <p lang={lang} className="whitespace-pre-wrap">
        {children}
      </p>
      <GenerationBadge generation={generation} />
    </div>
  );
}
