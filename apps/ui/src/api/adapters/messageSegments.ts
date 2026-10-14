// toMessageSegments (docs/ui-design.md 11.3, rule 10): splits a customer message into the three
// layers the preview shows apart: connective text (`phrase`), template fields filled by code
// (`field`) and the fixed legal clause (`clause`). Contract 1.1.0 carries the connective text
// with {terms}, {amount} and {deadline} placeholders, the field values, the clause kind and the
// exact text the customer receives; the clause is that text after the rendered body and a blank
// line. If the layers do not rebuild the stored text, the result is unverified and the UI shows
// the stored text plain (never a guessed split).
import type { LegalClauseKind, Lang, TemplateFieldName, TemplateFields } from '../models';

export type MessageSegment =
  | { kind: 'phrase'; text: string }
  | { kind: 'field'; name: TemplateFieldName; value: string | null }
  | { kind: 'clause'; clauseKind: LegalClauseKind; text: string };

export type MessageLayers =
  | { verified: true; lang: Lang; segments: MessageSegment[]; clauseKind: LegalClauseKind | null }
  | { verified: false; lang: Lang; text: string; reason: string };

export interface MessageSource {
  lang: Lang;
  connective_text: string;
  fields: TemplateFields;
  legal_clause_kind: LegalClauseKind | null;
}

const PLACEHOLDER = /\{(terms|amount|deadline)\}/;
const CLAUSE_SEPARATOR = '\n\n';

/** @param stored the text the customer receives: `rendered_text` of a draft, `body` of the outbox. */
export function toMessageSegments(message: MessageSource, stored: string): MessageLayers {
  const parts = message.connective_text.split(PLACEHOLDER);
  const segments: MessageSegment[] = [];
  let rendered = '';
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i] ?? '';
    if (i % 2 === 0) {
      if (part) segments.push({ kind: 'phrase', text: part });
      rendered += part;
      continue;
    }
    const name = part as TemplateFieldName;
    const value = message.fields[name] ?? null;
    if (value === null) {
      return { verified: false, lang: message.lang, text: stored, reason: `template field {${name}} has no value` };
    }
    segments.push({ kind: 'field', name, value });
    rendered += value;
  }
  if (stored === rendered) {
    if (message.legal_clause_kind !== null) {
      return { verified: false, lang: message.lang, text: stored, reason: 'the legal clause is missing from the stored text' };
    }
    return { verified: true, lang: message.lang, segments, clauseKind: null };
  }
  const prefix = rendered + CLAUSE_SEPARATOR;
  if (message.legal_clause_kind !== null && stored.startsWith(prefix) && stored.length > prefix.length) {
    segments.push({ kind: 'clause', clauseKind: message.legal_clause_kind, text: stored.slice(prefix.length) });
    return { verified: true, lang: message.lang, segments, clauseKind: message.legal_clause_kind };
  }
  return { verified: false, lang: message.lang, text: stored, reason: 'the layers do not rebuild the stored text' };
}
