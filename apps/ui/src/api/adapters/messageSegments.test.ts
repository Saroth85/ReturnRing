// T-MESSAGE (docs/ui-design.md 14.2, rule 10): the three layers split every message of the
// contract examples (the texts of each draft, the outbox, the decision results, the inspection
// confirmation) and rebuild its text exactly; a forged text is never split.
import { describe, expect, it } from 'vitest';
import type { CustomerMessageView, LegalClauseKind, OutboxMessage } from '@/api/models';
import { exampleFiles, readExample } from '@/test/examples';
import { draftSources, messageFor, outboxSource, toMessageSegments, type MessageSegment, type MessageSource } from './messageSegments';

type Found = { file: string; kind: 'draft' | 'outbox'; source: MessageSource };

function collectMessages(): Found[] {
  const found: Found[] = [];
  const visit = (file: string, node: unknown): void => {
    if (Array.isArray(node)) return node.forEach((n) => visit(file, n));
    if (!node || typeof node !== 'object') return;
    const n = node as Record<string, unknown>;
    if (Array.isArray(n.texts) && 'legal_clause_kind' in n && Array.isArray(n.sentence_ids)) {
      for (const source of draftSources({ message: n as unknown as CustomerMessageView })) found.push({ file, kind: 'draft', source });
    }
    if (typeof n.body === 'string' && typeof n.message_id === 'string' && n.fields) {
      found.push({ file, kind: 'outbox', source: outboxSource(n as unknown as OutboxMessage) });
    }
    Object.values(n).forEach((v) => visit(file, v));
  };
  for (const file of exampleFiles()) visit(file, readExample(file));
  return found;
}

const messages = collectMessages();

function rebuild(segments: MessageSegment[]): string {
  let body = '';
  let clause = '';
  for (const s of segments) {
    if (s.kind === 'phrase') body += s.text;
    else if (s.kind === 'field') body += s.value;
    else clause = s.text;
  }
  return clause ? `${body}\n\n${clause}` : body;
}

const withClause = () => messages.find((m) => m.source.legal_clause_kind !== null && m.source.fields.deadline !== null) as Found;

describe('toMessageSegments', () => {
  it('finds drafts and outbox messages in the examples, in both languages', () => {
    expect(messages.length).toBeGreaterThan(30);
    expect(messages.some((m) => m.kind === 'outbox')).toBe(true);
    expect(new Set(messages.map((m) => m.source.lang))).toEqual(new Set(['it', 'en']));
    for (const file of ['outbox.json', 'decision_result.marco_approved.json', 'inspection_confirm_response.json', 'case_card.elena.json']) {
      expect(messages.some((m) => m.file === file)).toBe(true);
    }
  });

  it.each(messages.map((m, i) => [`${m.file} #${i} ${m.kind} (${m.source.lang})`, m] as const))('splits and rebuilds %s', (_name, { source }) => {
    const layers = toMessageSegments(source);
    expect(layers.verified).toBe(true);
    if (!layers.verified) return;
    expect(rebuild(layers.segments)).toBe(source.text);
    const clauses = layers.segments.filter((s) => s.kind === 'clause');
    expect(clauses).toHaveLength(source.legal_clause_kind === null ? 0 : 1);
    // Every non-null field is one segment, in the order of the text.
    const fields = layers.segments.filter((s) => s.kind === 'field');
    expect(fields.map((f) => f.name).sort()).toEqual(
      Object.entries(source.fields)
        .filter(([, v]) => v !== null)
        .map(([k]) => k)
        .sort(),
    );
    // Reviewed sentences never carry a digit: every number is a template field or the clause.
    for (const s of layers.segments) if (s.kind === 'phrase') expect(s.text).not.toMatch(/\d/);
  });

  it('refuses a forged text', () => {
    const { source } = withClause();
    const forged = { ...source, text: source.text.replace(source.fields.deadline as string, 'whenever we like') };
    const layers = toMessageSegments(forged);
    expect(layers.verified).toBe(false);
    if (!layers.verified) expect(layers.text).toBe(forged.text);
  });

  it('refuses a message whose legal clause is missing', () => {
    const { source } = withClause();
    expect(toMessageSegments({ ...source, text: source.text.split('\n\n')[0] as string }).verified).toBe(false);
  });

  it('refuses a clause kind that does not match the text, and a field that appears twice', () => {
    const { source } = withClause();
    const kind: LegalClauseKind | null = null;
    expect(toMessageSegments({ ...source, legal_clause_kind: kind }).verified).toBe(false);
    const deadline = source.fields.deadline as string;
    const twice = source.text.replace(deadline, `${deadline} (${deadline})`);
    expect(toMessageSegments({ ...source, text: twice }).verified).toBe(false);
  });

  it('refuses overlapping fields', () => {
    const text = 'Refund of 10 euro by 10 October.';
    const layers = toMessageSegments({
      lang: 'en',
      legal_clause_kind: null,
      text,
      fields: { terms: null, amount: '10 euro', deadline: '10 euro by 10 October', deduction: null },
    });
    expect(layers.verified).toBe(false);
  });

  it('picks a language of a draft and maps an outbox message', () => {
    const draft = readExample<{ draft: { message: CustomerMessageView } }>('case_card.marco.json').draft;
    expect(messageFor(draft, 'en')?.text).toBe(draft.message.texts.find((t) => t.lang === 'en')?.text);
    expect(messageFor(null, 'it')).toBeNull();
    const outbox = readExample<{ items: OutboxMessage[] }>('outbox.json').items[0] as OutboxMessage;
    expect(outboxSource(outbox)).toEqual({ lang: outbox.lang, fields: outbox.fields, legal_clause_kind: outbox.legal_clause_kind, text: outbox.body });
  });
});
