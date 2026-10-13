import { Lock } from 'lucide-react';
import type { Lang, LegalClauseKind } from '@/api/models';
import { MESSAGE } from '@/copy/common';

interface LegalClauseProps {
  kind: LegalClauseKind;
  text: string;
  lang: Lang;
  asCustomer?: boolean;
}

/**
 * The fixed legal clause appended by code (rule 10, llm/legal_clause.py): a separate block with a
 * lock, never editable, never written by the language model (docs/ui-design.md 5.4).
 */
export function LegalClause({ kind, text, lang, asCustomer = false }: LegalClauseProps) {
  if (asCustomer) {
    return (
      <p lang={lang} data-legal-clause={kind} className="mt-4 whitespace-pre-wrap">
        {text}
      </p>
    );
  }
  return (
    <div
      data-legal-clause={kind}
      className="mt-4 rounded-r-card border-l-[3px] border-clause-rule bg-clause-bg px-3 py-2 text-clause-fg"
    >
      <p className="flex items-center gap-1 text-micro uppercase text-ink-2">
        <Lock aria-hidden="true" size={12} />
        {MESSAGE.clauseLabel[kind]}
      </p>
      <p lang={lang} className="mt-1 whitespace-pre-wrap">
        {text}
      </p>
      <p className="mt-1 text-caption text-ink-3">{MESSAGE.clauseCaption}</p>
    </div>
  );
}
