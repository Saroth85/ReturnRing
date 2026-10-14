// COPY-LINT for the manager area and the shared code it owns (docs/ui-design.md 10.2): none of the
// banned words of spec 16, and no number literals in copy (numbers come from data or constants).
import { describe, expect, it } from 'vitest';
import { CHARTS } from '@/components/charts';
import { ECONOMICS } from '@/components/economics';
import { RECORDS } from '@/components/records';
import { findBannedTerms, UI_COPY_BANNED } from '@/copy/bannedTerms';
import { MANAGER } from './copy';

function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value);
  else if (typeof value === 'function') out.push(String((value as (...a: string[]) => unknown)('x', 'y')));
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, out));
  return out;
}

/** Ids like ADR-11 or O-19 name documents, not quantities (the PROV-UI scan skips them too). */
const ID_TOKEN = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;

describe.each([
  ['MANAGER', MANAGER],
  ['ECONOMICS', ECONOMICS],
  ['CHARTS', CHARTS],
  ['RECORDS', RECORDS],
] as const)('%s copy', (_name, copy) => {
  const all = strings(copy);

  it('uses none of the banned words', () => {
    expect(findBannedTerms(all.join('\n'), UI_COPY_BANNED)).toEqual([]);
  });

  it('has no number literals', () => {
    const withDigits = all.filter((s) => /\d/.test(s.replace(ID_TOKEN, '')));
    expect(withDigits).toEqual([]);
  });
});
