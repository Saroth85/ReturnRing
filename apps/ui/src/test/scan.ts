// DOM scans shared by the tests: numbers without provenance (PROV-UI) and banned wording
// (spec 16). Both read what a person reads: text nodes, plus labels and titles that assistive
// technology announces.
import { findBannedTerms, RENDERED_TEXT_BANNED, type BannedMatch } from '@/copy/bannedTerms';
import { PROVENANCE_EXEMPT_SELECTOR, VERBATIM_SELECTOR } from './exempt';

export interface Unprovenanced {
  text: string;
  /** The closest element, as tag#id.class, to find it. */
  where: string;
}

function describe(el: Element | null): string {
  if (!el) return '(root)';
  const id = el.id ? `#${el.id}` : '';
  const data = Array.from(el.attributes)
    .filter((a) => a.name.startsWith('data-'))
    .map((a) => `[${a.name}]`)
    .join('');
  return `${el.tagName.toLowerCase()}${id}${data}`;
}

function textNodes(root: Node): Text[] {
  const doc = root.ownerDocument ?? (root as Document);
  const walker = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  const nodes: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node as Text);
  return nodes;
}

/**
 * Ids inside prose ("Your return RET-000107", "C-MARCO-1 of cluster CL-000417") are not numbers:
 * contract ids are uppercase prefixes joined by dashes, link ids join two of them with `~`.
 */
const ID_TOKEN = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;

/** Every visible text with a digit outside the exempt regions (PROV-UI). */
export function findUnprovenancedNumbers(root: Element): Unprovenanced[] {
  const found: Unprovenanced[] = [];
  for (const node of textNodes(root)) {
    const text = node.textContent ?? '';
    if (!/\d/.test(text.replace(ID_TOKEN, ''))) continue;
    const parent = node.parentElement;
    if (parent?.closest(PROVENANCE_EXEMPT_SELECTOR)) continue;
    found.push({ text: text.trim(), where: describe(parent) });
  }
  return found;
}

const ANNOUNCED_ATTRIBUTES = ['aria-label', 'title', 'alt', 'placeholder'];

export interface WordingReport {
  /** Banned words in UI-authored text. */
  errors: BannedMatch[];
  /** Banned words in contract or model text shown verbatim (reported, not failed). */
  warnings: BannedMatch[];
}

/** Banned wording of spec 16 in rendered text (the wording guard). */
export function findBannedWording(root: Element | string): WordingReport {
  if (typeof root === 'string') return { errors: findBannedTerms(root, RENDERED_TEXT_BANNED), warnings: [] };
  const authored: string[] = [];
  const verbatim: string[] = [];
  for (const node of textNodes(root)) {
    const text = node.textContent ?? '';
    if (!text.trim()) continue;
    (node.parentElement?.closest(VERBATIM_SELECTOR) ? verbatim : authored).push(text);
  }
  for (const el of Array.from(root.querySelectorAll('*'))) {
    for (const name of ANNOUNCED_ATTRIBUTES) {
      const value = el.getAttribute(name);
      if (value) (el.closest(VERBATIM_SELECTOR) ? verbatim : authored).push(value);
    }
  }
  return {
    errors: findBannedTerms(authored.join('\n'), RENDERED_TEXT_BANNED),
    warnings: findBannedTerms(verbatim.join('\n'), RENDERED_TEXT_BANNED),
  };
}

/** Throws when UI-authored rendered text uses a banned word (for tests). */
export function expectNoBannedWording(root: Element | string): void {
  const { errors } = findBannedWording(root);
  if (errors.length > 0) {
    const list = errors.map((e) => `"${e.match}" (${e.term}, ${e.lang})`).join(', ');
    throw new Error(`banned wording in rendered text (spec 16): ${list}`);
  }
}
