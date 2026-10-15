import { TriangleAlert } from 'lucide-react';
import { useId, useState } from 'react';
import { toMessageSegments, type MessageSource } from '@/api/adapters/messageSegments';
import { MESSAGE } from '@/copy/common';
import { LegalClause } from './LegalClause';
import { TemplateField } from './TemplateField';

interface MessagePreviewProps {
  /** One language of a draft (messageFor, draftSources) or an outbox message (outboxSource). */
  message: MessageSource;
  title?: string;
}

/**
 * A customer message in its three layers, which never mix (rule 10, docs/ui-design.md 5.4):
 * reviewed sentences, template fields filled by code, and the fixed legal clause. If the layers do
 * not split the text as rule 10 requires, the text is shown plain with an attention note.
 */
export function MessagePreview({ message, title = MESSAGE.title }: MessagePreviewProps) {
  const [asCustomer, setAsCustomer] = useState(false);
  const toggleId = useId();
  const layers = toMessageSegments(message);

  return (
    <section className="rounded-card border border-border bg-surface p-4" data-message-preview data-verbatim>
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-title">
          {title} · <span className="font-normal">{MESSAGE.langName[message.lang]}</span>
        </h3>
        <span className="inline-flex items-center gap-2 text-caption">
          <input id={toggleId} type="checkbox" checked={asCustomer} onChange={(e) => setAsCustomer(e.target.checked)} />
          <label htmlFor={toggleId}>{MESSAGE.previewAsCustomer}</label>
        </span>
      </header>
      {layers.verified ? (
        <div className="max-w-message text-message">
          <p lang={layers.lang} className="whitespace-pre-wrap">
            {layers.segments.map((segment, i) =>
              segment.kind === 'phrase' ? (
                <span key={i}>{segment.text}</span>
              ) : segment.kind === 'field' ? (
                <TemplateField key={i} name={segment.name} value={segment.value} asCustomer={asCustomer} />
              ) : null,
            )}
          </p>
          {layers.segments.map((segment, i) =>
            segment.kind === 'clause' ? (
              <LegalClause key={i} kind={segment.clauseKind} text={segment.text} lang={layers.lang} asCustomer={asCustomer} />
            ) : null,
          )}
          {layers.clauseKind === null && !asCustomer && <p className="mt-3 text-caption text-ink-3">{MESSAGE.noClause}</p>}
          {!asCustomer && <p className="mt-3 text-caption text-ink-3">{MESSAGE.legend}</p>}
        </div>
      ) : (
        <div className="max-w-message text-message" data-unverified>
          <p className="mb-2 flex items-center gap-2 rounded-control bg-attn-bg px-2 py-1 text-caption text-attn-fg">
            <TriangleAlert aria-hidden="true" size={14} />
            {MESSAGE.unverified}
          </p>
          <p lang={layers.lang} className="whitespace-pre-wrap">
            {layers.text}
          </p>
        </div>
      )}
    </section>
  );
}
