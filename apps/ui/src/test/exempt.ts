// What may contain a digit without its own provenance (PROV-UI, docs/ui-design.md 14.2): a
// Figure, a provenance scope, an id, a date, a version, a named constant, a template field, the
// legal clause, generated or verbatim contract text, the open provenance popover, the mode banner
// (product names) and a legal reference. Shared by the unit tests and the Playwright smoke test.
export const PROVENANCE_EXEMPT_SELECTOR = [
  '[data-figure]',
  '[data-provenance-scope]',
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
  'script',
  'style',
  'noscript',
].join(',');

/** Text inside these is contract or model text shown verbatim: banned words there are warnings. */
export const VERBATIM_SELECTOR = '[data-verbatim],[data-generated]';
