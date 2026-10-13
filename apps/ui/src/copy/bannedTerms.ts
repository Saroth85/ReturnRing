// Wording to avoid (spec section 16, "Formulazioni da evitare"; docs/ui-design.md 10.2). The
// ReturnRing brand is allowed; everything below is matched as whole words, any case.

export interface BannedTerm {
  /** The term as listed. */
  term: string;
  /** Regex source of the term and its inflections, without boundaries. */
  pattern: string;
  lang: 'en' | 'it';
}

/** Never in rendered text: the accusatory words of spec 16 (wording guard, tests). */
export const RENDERED_TEXT_BANNED: readonly BannedTerm[] = [
  { term: 'fraud', pattern: 'frauds?', lang: 'en' },
  { term: 'fraudulent', pattern: 'fraudulent(?:ly)?', lang: 'en' },
  { term: 'scam', pattern: 'scam(?:s|mers?)?', lang: 'en' },
  { term: 'suspicious', pattern: 'suspicious(?:ly)?', lang: 'en' },
  { term: 'abuser', pattern: 'abusers?', lang: 'en' },
  { term: 'cheat', pattern: 'cheat(?:s|ed|ing|ers?)?', lang: 'en' },
  { term: 'same person', pattern: 'same\\s+person', lang: 'en' },
  { term: 'frode', pattern: 'frod[ei]', lang: 'it' },
  { term: 'truffa', pattern: 'truff[ae]', lang: 'it' },
  { term: 'sospetto', pattern: 'sospett[oaie]', lang: 'it' },
  { term: 'abusatore', pattern: 'abusator[ei]', lang: 'it' },
  { term: 'stessa persona', pattern: 'stessa\\s+persona', lang: 'it' },
];

/**
 * Never in UI-authored strings (copy files, JSX literals): the longer list of docs/ui-design.md
 * 10.2, for the COPY-LINT test. Contract and model text is shown verbatim and is not checked here.
 */
export const UI_COPY_BANNED: readonly BannedTerm[] = [
  ...RENDERED_TEXT_BANNED,
  ...(
    [
      ['suspect', 'suspects?'],
      ['abusive', 'abusive'],
      ['abuse', 'abuse[sd]?'],
      ['culprit', 'culprits?'],
      ['caught', 'caught'],
      ['blacklist', 'blacklist(?:s|ed)?'],
      ['ban', 'bann?(?:ed|s)?'],
      ['blocked', 'blocked'],
      ['flagged customer', 'flagged\\s+customers?'],
      ['same customer', 'same\\s+customer'],
      ['identity match', 'identity\\s+match(?:es)?'],
      ['alias', 'alias(?:es)?'],
      ['bad customer', 'bad\\s+customers?'],
      ['good customer', 'good\\s+customers?'],
      ['trusted', 'trusted'],
      ['untrusted', 'untrusted'],
      ['VIP', 'vips?'],
      ['ring', 'rings?'],
      ['AI decided', 'ai\\s+decided'],
      ['Gemma recommends', 'gemma\\s+recommends'],
      ['savings', 'savings'],
      ['money saved', 'money\\s+saved'],
      ['legal by construction', 'legal\\s+by\\s+construction'],
      ['compliant', 'compliant'],
      ['certified', 'certified'],
    ] as const
  ).map(([term, pattern]) => ({ term, pattern, lang: 'en' as const })),
  { term: 'abuso', pattern: 'abus[oi]', lang: 'it' },
];

export interface BannedMatch {
  term: string;
  lang: 'en' | 'it';
  match: string;
  index: number;
}

/** Whole-word, case-insensitive matches of a list in a text (letters and digits are word characters). */
export function findBannedTerms(text: string, list: readonly BannedTerm[] = RENDERED_TEXT_BANNED): BannedMatch[] {
  const found: BannedMatch[] = [];
  for (const entry of list) {
    const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${entry.pattern})(?![\\p{L}\\p{N}])`, 'giu');
    for (const m of text.matchAll(re)) {
      found.push({ term: entry.term, lang: entry.lang, match: m[0], index: m.index ?? 0 });
    }
  }
  return found.sort((a, b) => a.index - b.index);
}
