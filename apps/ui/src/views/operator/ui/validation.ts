// Client-side checks of the sheets (docs/ui-design.md 6.3.4): a reason of at least three
// non-blank characters. The server validates again (422 invalid_request).

import { MIN_REASON_CHARS } from '@/api/contract.gen';

/** The minimum length of a typed reason, trimmed (docs/ui-design.md 6.3.4; contracts/vocab.py). */
export { MIN_REASON_CHARS };

export const reasonValid = (text: string): boolean => text.trim().length >= MIN_REASON_CHARS;
