// T-MESSAGE (docs/ui-design.md 14.2): the three layers rebuild the stored text of every message
// in the contract examples; a forged text is never split.
import { describe, expect, it } from 'vitest';
import type { CustomerMessageView, OutboxMessage } from '@/api/models';
import { exampleFiles, readExample } from '@/test/examples';
import { toMessageSegments, type MessageSegment } from './messageSegments';

type Found = { file: string; message: CustomerMessageView | OutboxMessage; stored: string };

function collectMessages(): Found[] {
  const found: Found[] = [];
  const visit = (file: string, node: unknown): void => {
    if (Array.isArray(node)) return node.forEach((n) => visit(file, n));
    if (!node || typeof node !== 'object') return;
    const n = node as Record<string, unknown>;
    if (typeof n.connective_text === 'string' && n.fields && typeof n.lang === 'string') {
      const stored = typeof n.rendered_text === 'string' ? n.rendered_text : (n.body as string);
      found.push({ file, message: n as unknown as CustomerMessageView, stored });
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
    else if (s.kind === 'field') body += s.value ?? '';
    else clause = s.text;
  }
  return clause ? `${body}\n\n${clause}` : body;
}

describe('toMessageSegments', () => {
  it('finds drafts and outbox messages in the examples', () => {
    expect(messages.length).toBeGreaterThan(10);
    expect(messages.some((m) => 'body' in m.message)).toBe(true);
  });

  it.each(messages.map((m, i) => [`${m.file} #${i} (${m.message.lang})`, m] as const))('rebuilds %s', (_name, { message, stored }) => {
    const layers = toMessageSegments(message, stored);
    expect(layers.verified).toBe(true);
    if (!layers.verified) return;
    expect(rebuild(layers.segments)).toBe(stored);
    const clauses = layers.segments.filter((s) => s.kind === 'clause');
    expect(clauses).toHaveLength(message.legal_clause_kind === null ? 0 : 1);
    // Connective text never carries a digit: every number is a template field or the clause.
    for (const s of layers.segments) if (s.kind === 'phrase') expect(s.text).not.toMatch(/\d/);
  });

  it('refuses a forged stored text', () => {
    const { message, stored } = messages.find((m) => m.message.legal_clause_kind !== null) as Found;
    const forged = stored.replace(message.fields.terms, 'free shipping forever');
    const layers = toMessageSegments(message, forged);
    expect(layers.verified).toBe(false);
    if (!layers.verified) expect(layers.text).toBe(forged);
  });

  it('refuses a message whose legal clause is missing', () => {
    const { message, stored } = messages.find((m) => m.message.legal_clause_kind !== null) as Found;
    const withoutClause = stored.split('\n\n')[0] as string;
    expect(toMessageSegments(message, withoutClause).verified).toBe(false);
  });

  it('refuses a placeholder without a value', () => {
    const { message, stored } = messages.find((m) => m.message.connective_text.includes('{deadline}')) as Found;
    const layers = toMessageSegments({ ...message, fields: { ...message.fields, deadline: null } }, stored);
    expect(layers.verified).toBe(false);
  });
});
