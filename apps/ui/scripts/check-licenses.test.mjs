import { describe, expect, it } from 'vitest';
import { satisfies } from './check-licenses.mjs';

const runtime = new Set(['MIT', 'BSD-3-Clause', 'Apache-2.0', 'ISC']);

describe('SPDX expressions', () => {
  it.each([
    ['MIT', true],
    ['(MIT OR GPL-3.0)', true],
    ['MIT AND ISC', true],
    ['MIT AND GPL-3.0', false],
    ['(MIT AND (ISC OR MPL-2.0))', true],
    ['MPL-2.0', false],
    ['GPL-2.0 WITH Classpath-exception-2.0', false],
    ['Apache 2.0', true],
  ])('%s -> %s', (expression, expected) => {
    expect(satisfies(expression, runtime)).toBe(expected);
  });

  it('rejects malformed expressions', () => {
    expect(() => satisfies('(MIT', runtime)).toThrow();
  });
});
