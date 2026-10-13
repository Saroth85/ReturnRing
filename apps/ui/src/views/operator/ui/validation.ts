// Client-side checks of the sheets (docs/ui-design.md 6.3.4): a reason of at least three
// non-blank characters. The server validates again (422 invalid_request).

/** The minimum length of a typed reason, trimmed (docs/ui-design.md 6.3.4). */
export const MIN_REASON_CHARS = 3;

export const reasonValid = (text: string): boolean => text.trim().length >= MIN_REASON_CHARS;
