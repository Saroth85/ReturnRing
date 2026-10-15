// Agent area strings (docs/ui-design.md 7, 10.2). English; no number literals: the step limit and
// the tool list come from the contract, product names from src/copy/constants.ts.
import { AGENT_MAX_STEPS, AGENT_TOOLS } from '@/api/contract.gen';
import type { EntityRef } from '@/api/models';
import { LLM_NAME } from '@/copy/constants';

type Tool = (typeof AGENT_TOOLS)[number];

export const AGENT = {
  /** The drawer title; the model, as /readyz names it (./model.ts), follows in its own span. */
  title: (returnId: string) => `Ask about ${returnId} · read-only assistant`,
  model: {
    vllm: `(${LLM_NAME} on vLLM)`,
    mock: '(mock language model: hand-written stand-ins)',
    contract: '(contract examples)',
  },
  readOnly: 'It can read the case; it cannot change it.',
  footer: `Read-only: ${AGENT_TOOLS.length} tools, at most ${AGENT_MAX_STEPS} steps. Answers cannot change the decision.`,
  close: 'Close',
  panelLabel: 'Case assistant',
  /** The one polite announcement when a question is settled (docs/ui-design.md 10.3). */
  announce: {
    answer: 'Answer ready.',
    stopped: 'Stopped.',
    failed: 'The assistant could not answer. The decision is unaffected.',
  },
  conversationLabel: 'Questions and answers',

  intro: {
    title: 'Ask about this case',
    body: 'Pick a suggested question or write your own. The assistant reads the case with its tools and answers with the evidence it consulted and the evidence it is missing.',
    toolsTitle: 'What it can read',
  },

  tools: {
    get_case: { label: 'Read case', reads: 'the return, its order and the engine decision' },
    get_orders: { label: 'Read orders', reads: 'orders and returns of an account' },
    get_cluster: { label: 'Read cluster', reads: 'linked accounts and the identifiers they share' },
    get_score_factors: { label: 'Read score factors', reads: 'contributions to the score, not causes' },
    get_policy_text: { label: 'Read policy text', reads: 'the terms of an order and the return policy' },
  } satisfies Record<Tool, { label: string; reads: string }>,

  /** Tool argument names in words; unknown names fall back to the name with spaces. */
  argumentNames: {
    customer_id: 'customer',
    return_id: 'return',
    order_id: 'order',
    terms_snapshot_id: 'terms snapshot',
    cluster_id: 'cluster',
    link_id: 'link',
    variant_id: 'variant',
  } as Record<string, string>,

  suggestedTitle: 'Suggested questions',
  /**
   * The three questions of US-702, worded as the contract examples ask them
   * (agent_ask_request.*.json). The link question names a link of the case's own linked accounts
   * (questions.ts), so it exists only for a case with linked accounts.
   */
  suggested: {
    link: (a: string, b: string) => `Which evidence supports the link between ${a} and ${b}?`,
    rossi: 'What makes this case different from the Rossi family?',
    terms: 'Which terms applied when the order was placed?',
  },

  composer: {
    label: 'Your question about this case',
    placeholder: 'Ask a question about this case…',
    ask: 'Ask',
    askKey: 'Enter',
    stop: 'Stop',
    // Contract 1.5.0: in the cached demo the agent answers 503 (no model runs). Where precomputed
    // outputs were made is readyz.label's claim, never this copy's (ADR-15 section 17).
    cached: 'The case assistant needs the live model, which is off in the cached demo.',
    cachedTitle: 'Unavailable in the cached demo',
    rateLimited: 'Too many questions in a short time.',
    retryAfter: (seconds: number) => `The service asks to wait ${seconds} s before the next question.`,
    coolingDown: (seconds: number) => `New questions resume in ${seconds} s.`,
  },

  turn: {
    askedBy: (who: string) => `Asked by ${who}`,
    stepsTitle: 'Steps',
    step: (n: number) => `Step ${n}`,
    stepLimit: `of at most ${AGENT_MAX_STEPS}`,
    toolCalls: 'Tool calls',
    toolCallsLabel: 'Tool calls the assistant used for this answer',
    running: 'Reading…',
    ok: 'Done',
    failed: 'Failed',
    writing: 'Writing the answer…',
    answerTitle: 'Answer',
    answerNote: 'cannot change the decision',
    stopped: 'Stopped before the answer was complete. What arrived is shown as received.',
    consulted: 'Evidence consulted',
    missing: 'Evidence missing',
    noneConsulted: 'None listed.',
    noneMissing: 'None listed by the assistant.',
    alsoCited: 'Also cited in the answer',
    argumentsLabel: 'Arguments',
  },

  refusal: {
    write_request: { title: 'The assistant only reads the case.', body: 'Approvals and changes are made on the case card.' },
    /** GenerationInfo.fallback_reason write_request (ADR-15 section 15): code refused it, no model ran. */
    byCode: 'Refused by code before any model call',
    out_of_scope: {
      title: 'This question is outside this case.',
      body: 'The assistant answers questions about this return from its read-only tools: case, orders, linked accounts, score factors and policy text.',
    },
    step_limit: {
      title: 'The assistant reached its step limit.',
      body: `It reads at most ${AGENT_MAX_STEPS} times per question. Try a narrower question.`,
    },
    replied: 'The assistant replied',
    goToDecision: 'Go to the decision panel',
  },

  errors: {
    unaffected: 'The decision is unaffected: the case card works without the assistant.',
    timeout: 'The assistant did not answer in time.',
    internal: 'The assistant stopped with an internal error.',
    serviceSaid: 'Service message',
    notFound: (returnId: string) => `Return ${returnId} not found`,
    forbiddenAction: 'ask the case assistant',
    askAgain: 'Ask again',
    requestId: 'Request id',
  },

  issues: {
    title: 'This answer did not arrive as the contract describes; it is shown as received.',
    out_of_order: 'Events arrived out of the contract order.',
    seq_gap: 'Some events of the stream are missing.',
    step_limit_exceeded: `More steps than the limit of ${AGENT_MAX_STEPS}.`,
    unpaired_tool_call: 'A tool call has no result.',
    steps_used_mismatch: 'The step count reported by the service differs from the steps received.',
    text_mismatch: 'The streamed text differs from the final answer; the final answer is shown.',
    no_final: 'The stream closed without an answer, a refusal or an error.',
    no_done: 'The stream ended before it was complete.',
  },

  steps: {
    originExample: (generationId: string) => `Contract example: steps_used of agent stream ${generationId}`,
    originMockModel: (generationId: string) =>
      `Mock language model (hand-written stand-in): steps_used of agent stream ${generationId}`,
    originLive: (generationId: string, model: string, cached: boolean) =>
      `${model} on vLLM: steps_used of agent stream ${generationId}${cached ? ', precomputed' : ''}`,
  },

  refs: {
    kinds: {
      return: 'Return',
      order: 'Order',
      order_line: 'Order line',
      customer: 'Customer',
      link: 'Link',
      cluster: 'Cluster',
      variant: 'Variant',
      anomaly: 'Product issue',
      catalog_task: 'Catalogue task',
      terms_snapshot: 'Terms snapshot',
      decision: 'Decision',
    } satisfies Record<EntityRef['kind'], string>,
    showOnCard: (kind: string, id: string) => `Show ${kind.toLowerCase()} ${id} on the case card`,
    open: (kind: string, id: string) => `Open ${kind.toLowerCase()} ${id}`,
  },

  harnessTitle: 'Agent harness',
  harnessIntro: 'Dev only: the case assistant without the case card, over the streamed answer of POST /api/agent/ask.',
  harness: {
    returnLabel: 'Return',
    pickReturn: 'Name a return with ?return= : the demo cases, which give the Marco case, are not available.',
    openCase: 'Open the case card',
    openDrawer: 'Open the drawer as on the case card',
    drawerTitle: 'Drawer',
    drawerBody: 'The operator area mounts the same component as a right drawer; its cited refs call onRefSelect.',
    focusTitle: 'Focus requests',
    focusEmpty: 'Click a cited id in an answer: on the case card it scrolls to that link, account or order and highlights it.',
    focusOpen: 'Open on the case card',
    eventsTitle: 'Stream events of the latest question',
    eventsEmpty: 'No question asked yet.',
  },
};
