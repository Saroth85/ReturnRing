// Shared UI strings (docs/ui-design.md 10.2), in English. Area strings live in
// src/views/<area>/copy.ts. No number literals: numbers come from data or src/copy/constants.ts.
import type { ClockKind, DisplayNature, FallbackReason, Lang, LegalClauseKind, TemplateFieldName } from '@/api/models';
import { GPU_NAME, GUARANTEE_DIRECTIVE, LLM_NAME, LLM_RETRY_SECONDS, SYNTHETIC_SEED } from './constants';

export const APP_NAME = 'ReturnRing';
export const SHOP_LINE = 'Navigli Fashion (fictional shop, synthetic data)';

export const NATURE_WORD: Record<DisplayNature, string> = {
  real: 'Real',
  synthetic: 'Synthetic',
  simulated: 'Simulated',
  illustrative: 'Illustrative',
};

/** One-line meaning of each nature (docs/ui-design.md 5.1; explained once on /about). */
export const NATURE_MEANING: Record<DisplayNature, string> = {
  real: 'Measured on real data (ASOS GraphReturns, CC BY 4.0) or a real measurement in this browser.',
  synthetic: `Computed on the Navigli Fashion synthetic shop (seed ${SYNTHETIC_SEED}).`,
  simulated: 'Estimated by the economic simulator under stated assumptions.',
  illustrative: 'A placeholder until measured.',
};

export const PROVENANCE = {
  nature: 'Nature',
  origin: 'Origin',
  period: 'Period',
  whatNaturesMean: 'What do natures mean?',
  copy: 'Copy provenance',
  copied: 'Provenance copied',
  footerPrefix: 'Numbers in this section:',
  compactLabel: (covers: string) => `Numbers of ${covers}`,
  compactButton: (covers: string) => `Origin and period of ${covers}`,
  contractExamples: 'Mock data from contract examples, not results.',
  toggle: 'Provenance',
  toggleHint: 'Show the nature and period next to every figure',
  thresholdWord: 'threshold',
  copyId: 'Copy',
  copiedId: 'Copied',
};

export const MODE = {
  liveTitle: 'LIVE',
  liveGpu: `${GPU_NAME} · ${LLM_NAME} on vLLM`,
  liveCpuTitle: 'LIVE (CPU)',
  liveCpu: 'mock language model: drafts are hand-written stand-ins',
  cachedTitle: 'CACHED DEMO',
  cachedTail: 'GPU off: nothing is computed live',
  contractTitle: 'CONTRACT EXAMPLES',
  contractLine: 'UI running on contracts/examples/api',
  contractSuffix: 'contract examples',
  mixed: 'MIXED MODE: some responses are cached',
  checking: 'Checking the service mode',
  unknownTitle: 'MODE UNKNOWN',
  unknown: '/readyz did not answer',
  readOnly: 'read-only public demo',
  degradedPrefix: 'Not every component is ready:',
  cornerCached: 'Cached demo',
  cornerContract: 'Contract examples',
  titleCached: '[Cached]',
  titleMock: '[Mock]',
  llmReady: 'LLM ready',
  llmContract: 'LLM: contract example',
  llmMock: 'LLM: mock stand-in (no model)',
  llmCached: 'LLM off · precomputed texts and templates in use',
  llmOff: 'LLM off · templates in use',
  llmUnavailable: 'LLM unavailable · templates in use',
  bannerLabel: 'Service mode',
  contractMismatchTitle: 'Contract mismatch',
  contractMismatch: (server: string, ui: string) =>
    `the API serves contract ${server}, this UI was built for ${ui}: some screens may fail. Regenerate the UI types (npm run gen:types) and rebuild.`,
  scenarioLabel: 'Mock scenario',
  /** The service's clock (/readyz `clock`, ADR-15 sections 15, 19, 20); its note is the tooltip. */
  clockKind: { demo: 'demo clock', system: 'wall clock', fixed: 'fixed clock' } satisfies Record<ClockKind, string>,
  clockFrozen: 'frozen at the snapshot',
  clockRunning: 'running since the reset',
  clockSnapshot: 'snapshot',
};

export const FEEDBACK = {
  errorTitle: 'The service did not answer.',
  refusedTitle: 'The service refused the request.',
  violationsTitle: 'What to correct',
  retry: 'Retry',
  requestId: 'Request id',
  code: 'Code',
  loading: 'Loading',
  llmUnavailable:
    'The language model is unavailable. The current draft stays valid, template messages still work, and the decision does not depend on it.',
  llmRetryIn: (seconds: number) => `Retry in ${seconds} s`,
  llmRetrySeconds: LLM_RETRY_SECONDS,
  forbidden: (role: string, action: string) =>
    `Your role (${role}) can't ${action}. Switch user under 'Acting as' to demonstrate another persona.`,
  readOnlyDemo: 'Read-only public demo',
  readOnlyBody: 'Nothing was saved: this public demo only reads. Every write is refused here, whoever acts.',
  roleReason: (allowed: string, role: string) => `This action is for ${allowed} (${role} can view).`,
  notFoundTitle: 'Page not found',
  notFoundBody: 'This address is not a page of ReturnRing.',
  backToStart: 'Back to the start page',
  placeholderNote: 'Placeholder: the screen is built in this area. Data below comes from the API client.',
  dataLoaded: 'answered',
  dataLoading: 'loading',
  dataFailed: 'failed',
};

export const MESSAGE = {
  /** The refund a sent message states, read with its provenance (OutboxMessage.refund). */
  refundStated: 'Refund stated in the message:',
  fieldLabels: { terms: 'TERMS', amount: 'AMOUNT', deadline: 'DEADLINE', deduction: 'DEDUCTION' } satisfies Record<
    TemplateFieldName,
    string
  >,
  /** Where code takes each field from (TemplateFields, llm.messages.template_fields). */
  fieldSource: {
    terms: 'future_terms',
    amount: 'the refund: price paid for the returned line less the diminished-value claim',
    deadline: 'refund_due_by',
    deduction: 'diminished_value_claim',
  } satisfies Record<TemplateFieldName, string>,
  fieldTooltip: (name: string, source: string) =>
    `{${name}}: filled by code from the decision (${source}), never written by the language model`,
  missingField: (name: string) => `[missing ${name}]`,
  clauseLabel: { withdrawal: 'Fixed legal clause · withdrawal', guarantee: 'Fixed legal clause · guarantee' } satisfies Record<
    LegalClauseKind,
    string
  >,
  clauseCaption: 'Fixed text from llm/legal_clause.py, added by code, cannot be edited',
  noClause: 'No legal clause: terms are not restrictive.',
  legend: 'Legend: plain text = reviewed sentences · FIELD = template field filled by code · bar = fixed legal text',
  previewAsCustomer: 'Preview as the customer',
  unverified: 'Message layers could not be verified; showing the text as stored.',
  langName: { it: 'Italiano', en: 'English' } satisfies Record<Lang, string>,
  title: 'Customer message',
  /** No message in the language asked for: the draft holds only the others. */
  noTextInLang: (lang: string) => `No message in ${lang} in this draft.`,
  generation: {
    // The model is the generation record's (generation.model, or the mock model): never a fixed name.
    firstCall: (model: string) => `${model} · validated on the first call`,
    secondCall: (model: string) => `${model} · validated on the second call`,
    template: 'Reviewed template, no model text',
    mockModel: 'Mock language model (hand-written stand-in)',
    // Where precomputed outputs were made is the claim of readyz.label alone (ADR-15 section 17).
    cached: 'Precomputed (cached demo)',
    fallback: {
      decision_echo: 'the model output restated a different decision',
      invalid_twice: 'the model output failed the validator twice',
      unavailable: 'the language model was unavailable',
      write_request: 'a request to change something: refused by code before any model call',
    } satisfies Record<FallbackReason, string>,
    modelCalls: (count: number) => `${count} model call${count === 1 ? '' : 's'}`,
  },
};

export const DECISION = {
  quadrantTip: (q: string, rule: string) => `${q}: ${rule}. A position in the matrix, not a judgement of the customer.`,
  quadrantRule: {
    Q1: { risk: 'below', value: 'at or above' },
    Q2: { risk: 'below', value: 'below' },
    Q3: { risk: 'at or above', value: 'at or above' },
    Q4: { risk: 'at or above', value: 'below' },
  } as Record<'Q1' | 'Q2' | 'Q3' | 'Q4', { risk: string; value: string }>,
  quadrantPlain: {
    Q1: 'higher value · lower risk score',
    Q2: 'lower value · lower risk score',
    Q3: 'higher value · higher risk score',
    Q4: 'lower value · higher risk score',
  } as Record<'Q1' | 'Q2' | 'Q3' | 'Q4', string>,
  path: { withdrawal: 'Withdrawal', guarantee: 'Guarantee' } as Record<'withdrawal' | 'guarantee', string>,
  guaranteeOutcome: 'Guarantee path · not scored',
  insufficientOutcome: 'Not enough evidence · standard terms',
  evidence: { sufficient: 'Evidence sufficient', insufficient: 'Not enough evidence' } as Record<
    'sufficient' | 'insufficient',
    string
  >,
  needsApproval: 'Needs approval',
  needsYourApproval: 'Needs your approval',
  reviewTip: (days: number) =>
    `${days} days after the decision (project assumption). The restriction lapses at this date unless a person renews it.`,
  noReview: 'no review date: not a restriction',
  refundDueBy: 'Refund due by',
  withdrawalIntact: 'withdrawal right intact on every order',
  guaranteeFloor: 'Guarantee path · free remedy under',
  guaranteeLaw: GUARANTEE_DIRECTIVE,
  reviewWord: 'review',
};

export const SHELL = {
  actingAs: 'Acting as',
  asOf: 'as of',
  theme: 'Theme',
  themes: { system: 'System', light: 'Light', dark: 'Dark' } as Record<'system' | 'light' | 'dark', string>,
  navLabel: 'Main',
  /** The visible toggle of the navigation drawer under 1024 px (UI35). */
  menu: 'Menu',
  shortcuts: 'Keyboard shortcuts',
  shortcutsClose: 'Close',
  shortcutsHint: 'Single keys work when the focus is not in a text field.',
  shortcutGroups: [
    {
      scope: 'Everywhere',
      keys: [
        ['?', 'This list'],
        ['/', 'Focus search or filter'],
        ['P', 'Provenance mode: nature and period next to every figure'],
        ['T', 'GPU dock'],
      ],
    },
    { scope: 'Presenter mode', keys: [['Alt+← · Alt+→', 'Previous or next demo beat']] },
    {
      scope: 'Queue',
      keys: [
        ['J · K · ↓ · ↑', 'Move the selection'],
        ['Enter', 'Open the case'],
      ],
    },
    {
      scope: 'Case',
      keys: [
        ['[ · ]', 'Previous or next case of the queue view'],
        ['A', 'Approve: popover or sheet'],
        ['O · R · L', 'Override · reject · review'],
        ['E · D', 'Add favourable evidence · dispute the selected link'],
        ['I · M', 'Ask about this case · message language'],
      ],
    },
    {
      scope: 'Sheets',
      keys: [
        ['Enter', 'Confirms only the non-restrictive popover and the recompute result'],
        ['Ctrl+Enter · Cmd+Enter', 'Confirm any other sheet'],
        ['Esc', 'Close (asks first when something was typed)'],
      ],
    },
    { scope: 'Inspection', keys: [['Y · N · U', 'Yes · no · not observable on the focused row']] },
  ] as ReadonlyArray<{ scope: string; keys: ReadonlyArray<readonly [string, string]> }>,
  navGroups: { work: 'Work', oversight: 'Oversight', demo: 'Demo', system: 'System' } as Record<
    'work' | 'oversight' | 'demo' | 'system',
    string
  >,
  skipToContent: 'Skip to content',
  roles: { operator: 'operator', manager: 'manager', warehouse: 'warehouse' } as Record<string, string>,
  collapseNav: 'Collapse navigation',
  expandNav: 'Expand navigation',
  badgeOf: (label: string) => `${label}: number of returns`,
  /** The service's 'now' in the top bar (the /readyz clock, never the browser's date). */
  serviceNow: 'service time',
  clockTip: (note: string) => `The service's clock: ${note}`,
  /** GET /api/demo/cases answered 404: no demo reset has stored the golden cases yet. */
  noDemoReset: {
    title: 'No demo reset yet',
    body: 'The golden cases of the demo are not in the store, so the demo path, the shop carts and the compare pair have no case to open. The API answered:',
    fix: 'Run python -m apps.api.reset (make reset), then reload the page.',
  },
};
