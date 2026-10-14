// COPY-LINT for the agent area (docs/ui-design.md 10.2): no banned word of spec 16 in the strings,
// no number literal in copy.ts (numbers come from the contract or named constants; ids such as
// C-MARCO-1 in the contract questions are ids, not numbers), and the limits match the contract.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { AGENT_TOOLS } from '@/api/contract.gen';
import { findBannedTerms, UI_COPY_BANNED } from '@/copy/bannedTerms';
import { EXAMPLES_DIR, readExample } from '@/test/examples';
import { AGENT } from './copy';
import { QUESTION_MAX_LENGTH } from './limits';

const ID_TOKEN = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;

function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value);
  else if (typeof value === 'function') out.push(String((value as (...a: unknown[]) => unknown)('x', true)));
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, out));
  return out;
}

describe('agent copy', () => {
  it('uses none of the banned words', () => {
    expect(findBannedTerms(strings(AGENT).join('\n'), UI_COPY_BANNED)).toEqual([]);
  });

  it('has no number literal in its string literals', () => {
    const source = readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'copy.ts'), 'utf8');
    const literals = [...source.matchAll(/(['`])((?:\\.|(?!\1).)*)\1/g)].map((m) => (m[2] ?? '').replace(/\$\{[^}]*\}/g, ''));
    const withDigits = literals.filter((text) => /\d/.test(text.replace(ID_TOKEN, '')));
    expect(withDigits).toEqual([]);
  });

  it('asks the three US-702 questions exactly as the contract examples do', () => {
    const asked = ['marco_link', 'marco_rossi', 'marco_terms'].map((name) => readExample<{ question: string }>(`agent_ask_request.${name}.json`).question);
    expect(AGENT.suggested).toEqual(asked);
  });

  it('names every contract tool in words', () => {
    expect(Object.keys(AGENT.tools).sort()).toEqual([...AGENT_TOOLS].sort());
  });
});

describe('agent limits', () => {
  it('QUESTION_MAX_LENGTH is the maxLength of AgentAskRequest.question', () => {
    const schemaPath = path.resolve(EXAMPLES_DIR, '../../json/api/agent_ask_request.schema.json');
    const schema = JSON.parse(readFileSync(schemaPath, 'utf8')) as { properties: { question: { maxLength: number } } };
    expect(QUESTION_MAX_LENGTH).toBe(schema.properties.question.maxLength);
  });
});
