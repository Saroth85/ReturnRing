// What may contain a digit without its own provenance (PROV-UI, docs/ui-design.md 14.2): a
// Figure, an id, a date, a version, a named constant, a template field, the
// legal clause, generated or verbatim contract text, the open provenance popover, the mode banner
// (product names) and a legal reference. Shared by the unit tests and the Playwright smoke test.
export const PROVENANCE_EXEMPT_SELECTOR = [
  '[data-figure]',
  '[data-id]',
  '[data-date]',
  '[data-version]',
  '[data-constant]',
  '[data-template-field]',
  '[data-legal-clause]',
  '[data-generated]',
  '[data-verbatim]',
  '[data-provenance-detail]',
  '[data-mode-banner]',
  '[data-legal-ref]',
  // Recharts measures label text in a hidden span appended to <body>.
  '#recharts_measurement_span',
  'script',
  'style',
  'noscript',
].join(',');

/**
 * A plain number inside a provenance scope is covered only when its nearest scope has a footer
 * (full or compact): the footer is where the origin, the period and the nature are reachable
 * (UI11). An outer scope's footer does not cover an inner scope's numbers.
 */
export const PROVENANCE_SCOPE_SELECTOR = '[data-provenance-scope]';
export const SCOPE_FOOTER_ATTRIBUTE = 'data-has-footer';

/** Text inside these is contract or model text shown verbatim: banned words there are warnings. */
export const VERBATIM_SELECTOR = '[data-verbatim],[data-generated]';
