// The wording guard (spec 16, "Formulazioni da evitare"): banned words fail in rendered text,
// in English and Italian; contract or model text shown verbatim is reported as a warning.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { findBannedTerms, RENDERED_TEXT_BANNED, UI_COPY_BANNED } from '@/copy/bannedTerms';
import * as common from '@/copy/common';
import { expectNoBannedWording, findBannedWording } from './scan';

describe('findBannedTerms', () => {
  it.each([
    'This looks like fraud',
    'a FRAUDULENT order',
    'probably a scam',
    'Suspicious activity',
    'the abuser',
    'customers who cheat',
    'the same person',
    'è una frode',
    'una truffa',
    'cliente sospetto',
    "l'abusatore",
    'la stessa persona',
  ])('flags "%s"', (text) => {
    expect(findBannedTerms(text)).not.toHaveLength(0);
  });

  it.each([
    'ReturnRing',
    'The mode banner is visible',
    'linked accounts that share identifiers',
    'risk score above the threshold',
    'suspend', // not "suspicious"
    'Abusers? no: abuse is not on the rendered list',
  ])('accepts "%s" on the rendered list unless it holds a listed word', (text) => {
    const found = findBannedTerms(text, RENDERED_TEXT_BANNED).map((m) => m.term);
    if (text.startsWith('Abusers')) expect(found).toEqual(['abuser']);
    else expect(found).toEqual([]);
  });

  it('matches whole words only', () => {
    expect(findBannedTerms('scampi and cheating', RENDERED_TEXT_BANNED).map((m) => m.match)).toEqual(['cheating']);
    expect(findBannedTerms('ReturnRing banner', UI_COPY_BANNED)).toEqual([]);
  });
});

describe('expectNoBannedWording on rendered text', () => {
  it('fails on UI-authored text, including labels', () => {
    const { container } = render(
      <div>
        <p>Approve</p>
        <button aria-label="Mark as suspicious">x</button>
      </div>,
    );
    expect(() => expectNoBannedWording(container)).toThrow(/suspicious/);
  });

  it('reports verbatim contract text as a warning, not an error', () => {
    const { container } = render(
      <div>
        <p data-verbatim>Not enough evidence: neither safe nor suspicious.</p>
      </div>,
    );
    const report = findBannedWording(container);
    expect(report.errors).toEqual([]);
    expect(report.warnings.map((w) => w.term)).toEqual(['suspicious']);
    expect(() => expectNoBannedWording(container)).not.toThrow();
  });

  it('passes on clean text', () => {
    render(<p>Linked accounts share identifiers; a person approves.</p>);
    expect(() => expectNoBannedWording(screen.getByText(/Linked accounts/))).not.toThrow();
  });
});

describe('shared copy (COPY-LINT seed)', () => {
  it('uses none of the banned words of docs/ui-design.md 10.2', () => {
    const strings: string[] = [];
    const collect = (value: unknown): void => {
      if (typeof value === 'string') strings.push(value);
      else if (typeof value === 'function') strings.push(String((value as (...a: string[]) => unknown)('x', 'y')));
      else if (value && typeof value === 'object') Object.values(value).forEach(collect);
    };
    collect(common);
    const found = findBannedTerms(strings.join('\n'), UI_COPY_BANNED);
    expect(found).toEqual([]);
  });
});
