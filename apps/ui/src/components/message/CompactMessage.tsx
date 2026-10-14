import { toMessageSegments, type MessageSource } from '@/api/adapters/messageSegments';
import { LegalClause } from './LegalClause';
import { TemplateField } from './TemplateField';

/**
 * A customer message in a small space (the confirm popover, docs/ui-design.md 6.3.1) with its three
 * layers kept (rule 10): the box scrolls instead of cutting the text, so no template field and no
 * part of the fixed legal clause is ever dropped (UI15). Unverified layers show the stored text.
 */
export function CompactMessage({ message, stored }: { message: MessageSource; stored: string }) {
  const layers = toMessageSegments(message, stored);
  return (
    <div className="max-h-48 overflow-y-auto rounded-control bg-surface-2 px-2 py-1 text-caption text-ink-1" data-compact-message data-verbatim>
      {layers.verified ? (
        <>
          <p lang={layers.lang} className="whitespace-pre-wrap">
            {layers.segments.map((segment, i) =>
              segment.kind === 'phrase' ? (
                <span key={i}>{segment.text}</span>
              ) : segment.kind === 'field' ? (
                <TemplateField key={i} name={segment.name} value={segment.value} />
              ) : null,
            )}
          </p>
          {layers.segments.map((segment, i) =>
            segment.kind === 'clause' ? <LegalClause key={i} kind={segment.clauseKind} text={segment.text} lang={layers.lang} compact /> : null,
          )}
        </>
      ) : (
        <p lang={layers.lang} className="whitespace-pre-wrap">
          {layers.text}
        </p>
      )}
    </div>
  );
}
