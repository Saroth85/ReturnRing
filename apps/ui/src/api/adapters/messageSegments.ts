// toMessageSegments (docs/ui-design.md 11.3, rule 10): splits a customer message into the three
// layers the preview shows apart: text written from the phrasebook (`phrase`), template fields
// filled by code (`field`) and the fixed legal clause (`clause`).
//
// Since contract 1.2.0 a message carries, per language, the template fields and the exact text
// the customer receives (llm.phrasebook.render_selection: opening, mandatory body, extras,
// closing, then a blank line and the clause). The split is checked, never guessed:
// - with a clause kind, the text is cut once at its first blank line: before it the body, after
//   it the clause (both non-empty); without one, the text has no blank line;
// - every non-null field value appears exactly once in the body, and no two of them overlap;
//   the body is cut at those positions, in order of position.
// Otherwise the result is unverified and the UI shows the text plain, with a note.
import type { DraftView, Lang, LegalClauseKind, OutboxMessage, TemplateFieldName, TemplateFields } from '../models';

export type MessageSegment =
  | { kind: 'phrase'; text: string }
  | { kind: 'field'; name: TemplateFieldName; value: string }
  | { kind: 'clause'; clauseKind: LegalClauseKind; text: string };

export type MessageLayers =
  | { verified: true; lang: Lang; segments: MessageSegment[]; clauseKind: LegalClauseKind | null }
  | { verified: false; lang: Lang; text: string; reason: string };

/** One message in one language: a draft's `texts[i]` with its clause kind, or an outbox message. */
export interface MessageSource {
  lang: Lang;
  fields: TemplateFields;
  legal_clause_kind: LegalClauseKind | null;
  /** The text the customer receives: `texts[i].text` of a draft, `body` of an outbox message. */
  text: string;
}

const CLAUSE_SEPARATOR = '\n\n';

function occurrences(haystack: string, needle: string): number[] {
  const found: number[] = [];
  for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, at + 1)) found.push(at);
  return found;
}

export function toMessageSegments(message: MessageSource): MessageLayers {
  const { lang, text, legal_clause_kind: clauseKind } = message;
  const unverified = (reason: string): MessageLayers => ({ verified: false, lang, text, reason });

  let body = text;
  let clause: string | null = null;
  const cut = text.indexOf(CLAUSE_SEPARATOR);
  if (clauseKind !== null) {
    if (cut < 0) return unverified('the legal clause is missing from the text');
    body = text.slice(0, cut);
    clause = text.slice(cut + CLAUSE_SEPARATOR.length);
    if (!body || !clause) return unverified('the body or the legal clause is empty');
  } else if (cut >= 0) {
    return unverified('a blank line in a message without a legal clause');
  }

  const spans: { start: number; end: number; name: TemplateFieldName; value: string }[] = [];
  for (const [name, value] of Object.entries(message.fields) as [TemplateFieldName, string | null][]) {
    if (value === null) continue;
    if (value === '') return unverified(`template field {${name}} is empty`);
    const at = occurrences(body, value);
    if (at.length !== 1) {
      return unverified(`template field {${name}} appears ${at.length === 0 ? 'nowhere' : `${at.length} times`} in the body`);
    }
    const start = at[0] as number;
    spans.push({ start, end: start + value.length, name, value });
  }
  spans.sort((a, b) => a.start - b.start);

  const segments: MessageSegment[] = [];
  let position = 0;
  for (const span of spans) {
    if (span.start < position) return unverified('two template fields overlap');
    if (span.start > position) segments.push({ kind: 'phrase', text: body.slice(position, span.start) });
    segments.push({ kind: 'field', name: span.name, value: span.value });
    position = span.end;
  }
  if (position < body.length) segments.push({ kind: 'phrase', text: body.slice(position) });
  if (clauseKind !== null && clause !== null) segments.push({ kind: 'clause', clauseKind, text: clause });
  return { verified: true, lang, segments, clauseKind };
}

/** Every language of a draft's message, in the order of `texts`. */
export function draftSources(draft: Pick<DraftView, 'message'> | null | undefined): MessageSource[] {
  if (!draft) return [];
  return draft.message.texts.map((t) => ({
    lang: t.lang,
    fields: t.fields,
    legal_clause_kind: draft.message.legal_clause_kind,
    text: t.text,
  }));
}

/** The draft's message in one language, or null when the draft has none in that language. */
export function messageFor(draft: Pick<DraftView, 'message'> | null | undefined, lang: Lang): MessageSource | null {
  return draftSources(draft).find((m) => m.lang === lang) ?? null;
}

/** An outbox message as a source: its body is the text the customer received. */
export function outboxSource(message: OutboxMessage): MessageSource {
  return { lang: message.lang, fields: message.fields, legal_clause_kind: message.legal_clause_kind, text: message.body };
}
