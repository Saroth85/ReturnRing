// The PROV-UI and wording scan of src/test/scan.ts, run in the page (shared by every spec). A
// plain number is covered by a Figure or another exempt region, or by its nearest provenance scope
// when that scope has a footer (UI11); banned words in verbatim contract or model text are
// reported apart.
import type { Page } from '@playwright/test';
import { RENDERED_TEXT_BANNED } from '../../src/copy/bannedTerms';
import { PROVENANCE_EXEMPT_SELECTOR, PROVENANCE_SCOPE_SELECTOR, SCOPE_FOOTER_ATTRIBUTE, VERBATIM_SELECTOR } from '../../src/test/exempt';

export interface PageScan {
  unprovenanced: string[];
  banned: string[];
  bannedVerbatim: string[];
  scrollWidth: number;
  width: number;
}

export async function scanPage(page: Page): Promise<PageScan> {
  const patterns = RENDERED_TEXT_BANNED.map((t) => t.pattern);
  return page.evaluate(
    ({ exempt, scopeSelector, footerAttribute, verbatim, patterns }) => {
      const idToken = /(?<![\p{L}\p{N}])[A-Z][A-Z0-9]*(?:[-~][A-Za-z0-9]+)+(?![\p{L}\p{N}])/gu;
      const banned = patterns.map((p) => new RegExp(`(?<![\\p{L}\\p{N}])(?:${p})(?![\\p{L}\\p{N}])`, 'iu'));
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const out = { unprovenanced: [] as string[], banned: [] as string[], bannedVerbatim: [] as string[] };
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent ?? '';
        const parent = node.parentElement;
        if (!text.trim() || !parent) continue;
        const covered = Boolean(parent.closest(exempt)) || Boolean(parent.closest(scopeSelector)?.hasAttribute(footerAttribute));
        if (/\d/.test(text.replace(idToken, '')) && !covered) out.unprovenanced.push(text.trim());
        if (banned.some((re) => re.test(text))) (parent.closest(verbatim) ? out.bannedVerbatim : out.banned).push(text.trim());
      }
      return { ...out, scrollWidth: document.documentElement.scrollWidth, width: document.documentElement.clientWidth };
    },
    {
      exempt: PROVENANCE_EXEMPT_SELECTOR,
      scopeSelector: PROVENANCE_SCOPE_SELECTOR,
      footerAttribute: SCOPE_FOOTER_ATTRIBUTE,
      verbatim: VERBATIM_SELECTOR,
      patterns,
    },
  );
}
