// COPY-LINT for the platform area (docs/ui-design.md 10.2, 14.2): the string literals of the copy
// files use none of the banned words of spec 16 and carry no number literals. Numbers in copy
// are interpolated from data or from named constants; ids (`RET-000102`, `US-403`) are not
// numbers, and the percentile names p50 and p95 are names.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { findBannedTerms, UI_COPY_BANNED } from '@/copy/bannedTerms';

const here = path.dirname(fileURLToPath(import.meta.url));
const COPY_FILES = ['copy.ts', 'shop/copy.ts', 'inspection/copy.ts', 'system/copy.ts'];

const ID_TOKEN = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;
const PERCENTILE = /(?<![\p{L}\p{N}])p(?:50|95|99)(?![\p{L}\p{N}])/gu;

/** The string literals of a TypeScript source, comments dropped, `${…}` holes removed. */
export function stringLiterals(source: string): string[] {
  const out: string[] = [];
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
  const re = /'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g;
  for (const m of code.matchAll(re)) {
    const text = m[1] ?? m[2] ?? (m[3] ?? '').replace(/\$\{[^}]*\}/g, ' ');
    out.push(text);
  }
  return out;
}

const imports = (literal: string) => literal.startsWith('@/') || literal.startsWith('./') || literal.startsWith('../');

describe('platform copy (COPY-LINT)', () => {
  it.each(COPY_FILES)('%s has no banned wording and no number literals', (file) => {
    const literals = stringLiterals(readFileSync(path.join(here, file), 'utf8')).filter((s) => !imports(s));
    expect(literals.length).toBeGreaterThan(0);
    const banned = literals.flatMap((s) => findBannedTerms(s, UI_COPY_BANNED).map((m) => `${m.match} in "${s}"`));
    expect(banned).toEqual([]);
    const numbers = literals.filter((s) => /\d/.test(s.replace(ID_TOKEN, '').replace(PERCENTILE, '')));
    expect(numbers).toEqual([]);
  });

  it('reads literals the way the check needs', () => {
    expect(stringLiterals("const a = 'x 1'; // 'y 2'\nconst b = `z ${n} w`;")).toEqual(['x 1', 'z   w']);
  });
});
