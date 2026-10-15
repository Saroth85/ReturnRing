// The case assistant over the mock adapter (contract examples, SSE frames without delay in tests):
// suggested questions, streamed steps, the answer with the evidence consulted and missing side by
// side, cited refs that focus the case card, the tool-call count through Figure, a neutral
// refusal, and every failure state (503, error event, 429 with Retry-After, 403, the cached demo
// up front). The link question is built from the case card's own links. US-701, US-702.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import { resetModesSeen } from '@/api/meta';
import type { AgentEvent, EntityRef } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp, renderWithProviders } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import { CaseAssistant } from './CaseAssistant';
import { AGENT } from './copy';

const RET = 'RET-000102';
// Marco's card: the strongest active link of C-MARCO-1 is C-MARCO-1~C-MARCO-3 (card, device, address).
const LINK_Q = AGENT.suggested.link('C-MARCO-1', 'C-MARCO-3');
const ROSSI_Q = AGENT.suggested.rossi;
const TERMS_Q = AGENT.suggested.terms;

afterEach(() => {
  act(() => {
    setScenario('default');
    setPersona('sara');
    resetModesSeen();
  });
});

function renderAssistant(props: { onRefSelect?: (ref: EntityRef) => void; onOpenChange?: (open: boolean) => void; open?: boolean } = {}) {
  const onOpenChange = props.onOpenChange ?? vi.fn();
  const view = renderWithProviders(<CaseAssistant returnId={RET} open={props.open ?? true} onOpenChange={onOpenChange} onRefSelect={props.onRefSelect} />);
  const dialog = screen.getByRole('dialog', { name: /Ask about RET-000102/ });
  return { ...view, dialog, onOpenChange };
}

const panel = () => document.querySelector<HTMLElement>('[data-agent-panel]') as HTMLElement;
const settled = () => waitFor(() => expect(panel().dataset.agentStream).not.toBe('streaming'));

function expectCleanScan(root: Element) {
  expect(findUnprovenancedNumbers(root), JSON.stringify(findUnprovenancedNumbers(root))).toEqual([]);
  expect(findBannedWording(root).errors).toEqual([]);
}

describe('CaseAssistant drawer', () => {
  it('opens read-only with the three suggested questions and the step limit', async () => {
    const { dialog } = renderAssistant();
    expect(dialog).toHaveTextContent('It can read the case; it cannot change it.');
    // On the mock adapter the answers are contract examples: no model is named (./model.ts).
    expect(dialog.querySelector('[data-assistant-model]')).toHaveTextContent('(contract examples)');
    expect(dialog).not.toHaveTextContent('Gemma');
    await within(dialog).findByRole('button', { name: LINK_Q });
    const suggested = within(dialog).getAllByRole('button').filter((b) => b.hasAttribute('data-suggested'));
    expect(suggested.map((b) => b.textContent)).toEqual([LINK_Q, ROSSI_Q, TERMS_Q]);
    expect(dialog).toHaveTextContent('Read-only: 5 tools, at most 5 steps. Answers cannot change the decision.');
    expect(within(dialog).getByText('Read score factors')).toBeInTheDocument();
    expectCleanScan(document.body);
  });

  it('streams the steps, then the answer with the evidence consulted and missing side by side', async () => {
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    expect(panel().dataset.agentStream).toBe('done');

    const steps = dialog.querySelectorAll('[data-agent-steps] > li');
    expect(steps).toHaveLength(2);
    expect(steps[0]).toHaveTextContent('Step 1 of at most 5');
    expect(steps[0]).toHaveTextContent('Read cluster');
    expect(steps[0]).toHaveTextContent('customer C-MARCO-1');
    expect(steps[1]).toHaveTextContent('Step 2');
    expect(steps[1]).toHaveTextContent('Read orders');

    const answer = dialog.querySelector('[data-agent-answer]') as HTMLElement;
    expect(answer).toHaveTextContent('Answer · cannot change the decision');
    expect(answer.querySelectorAll('[data-generated]')).toHaveLength(1);
    expect(answer.querySelector('[data-generated]')).toHaveTextContent('A link is a shared identifier, not proof of who uses the accounts.');
    expect(answer.querySelector('[aria-busy]')).toHaveAttribute('aria-busy', 'false');

    const consulted = answer.querySelector('[data-evidence="consulted"]') as HTMLElement;
    const missing = answer.querySelector('[data-evidence="missing"]') as HTMLElement;
    expect(within(consulted).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '/returns/RET-000102?focus=linked',
      '/returns/RET-000102?link=C-MARCO-1~C-MARCO-3',
      '/returns/RET-000102?focus=linked',
    ]);
    expect(missing).toHaveTextContent('No contact with the customer about the shared card');
    expectCleanScan(document.body);
  });

  it('shows the tool-call count through Figure, with its contract-example provenance', async () => {
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const count = dialog.querySelector('[data-tool-calls]') as HTMLElement;
    expect(count).toHaveTextContent(/Tool calls\s*2/);
    expect(count).toHaveTextContent('of at most 5');
    const figure = count.querySelector('[data-figure]') as HTMLElement;
    expect(figure).toHaveAttribute('data-nature', 'synthetic');
    expect(figure).toHaveTextContent('Contract example: steps_used of agent stream GEN-AGENT-000001');
  });

  it('a cited ref calls onRefSelect instead of navigating; on a narrow screen the drawer closes first', async () => {
    const onRefSelect = vi.fn();
    const { dialog, onOpenChange } = renderAssistant({ onRefSelect });
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: false } as MediaQueryList);
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const chip = within(dialog.querySelector('[data-evidence="consulted"]') as HTMLElement).getByRole('link', {
      name: 'Show link C-MARCO-1~C-MARCO-3 on the case card',
    });
    await userEvent.click(chip);
    expect(onRefSelect).toHaveBeenCalledWith({ kind: 'link', id: 'C-MARCO-1~C-MARCO-3' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('on a wide screen the drawer stays open beside the card', async () => {
    const onRefSelect = vi.fn();
    const { dialog, onOpenChange } = renderAssistant({ onRefSelect });
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
    await userEvent.click(within(dialog).getByRole('button', { name: TERMS_Q }));
    await settled();
    await userEvent.click(within(dialog).getAllByRole('link', { name: 'Show order ORD-005230 on the case card' })[0] as HTMLElement);
    expect(onRefSelect).toHaveBeenCalledWith({ kind: 'order', id: 'ORD-005230' });
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('keeps earlier questions above the latest (session history)', async () => {
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    // After the first question the suggestions fold behind a disclosure, leaving the answer room (UI34).
    const disclosure = within(dialog).getByRole('button', { name: 'Suggested questions' });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(disclosure);
    await userEvent.click(within(dialog).getByRole('button', { name: ROSSI_Q }));
    await settled();
    const turns = dialog.querySelectorAll('article[data-turn]');
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveTextContent(LINK_Q);
    expect(turns[1]).toHaveTextContent(ROSSI_Q);
    expect(turns[1]).toHaveTextContent('shares an address and a device');
    expect(turns[1]).toHaveTextContent('None listed by the assistant.');
  });

  it('on a phone the suggested questions fold after a question, behind a disclosure', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) => ({ matches: query === '(max-width: 639px)', addEventListener() {}, removeEventListener() {} }) as unknown as MediaQueryList,
    );
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const disclosure = within(dialog).getByRole('button', { name: 'Suggested questions' });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(within(dialog).queryByRole('button', { name: ROSSI_Q })).toBeNull();
    await userEvent.click(disclosure);
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    await userEvent.click(within(dialog).getByRole('button', { name: ROSSI_Q }));
    await settled();
    expect(within(dialog).getByRole('button', { name: 'Suggested questions' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('answers a typed question with Enter', async () => {
    const { dialog } = renderAssistant();
    const input = within(dialog).getByRole('textbox', { name: 'Your question about this case' });
    await userEvent.type(input, `${TERMS_Q}{Enter}`);
    await settled();
    expect(input).toHaveValue('');
    expect(dialog.querySelector('[data-generated]')).toHaveTextContent('These terms stay valid for this order');
  });

  it('refuses a write request neutrally and points to the decision panel', async () => {
    const onRefSelect = vi.fn();
    const { dialog, onOpenChange } = renderAssistant({ onRefSelect });
    await userEvent.type(within(dialog).getByRole('textbox'), 'Approve the refund and remove the restriction.{Enter}');
    await settled();
    const refusal = dialog.querySelector('[data-agent-refusal="write_request"]') as HTMLElement;
    expect(refusal).toHaveTextContent('The assistant only reads the case.');
    expect(refusal).toHaveTextContent('Approvals and changes are made on the case card.');
    expect(refusal.querySelector('[data-generated]')).toHaveTextContent('I can only read the case.');
    // fallback_reason write_request: code refused it before any model call (ADR-15 section 15).
    expect(refusal.querySelector('[data-refused-by-code]')).toHaveTextContent('Refused by code before any model call');
    expect(refusal).toHaveTextContent('0 model calls');
    // Neutral: no alert role, no danger or attention styling.
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(refusal.className).not.toMatch(/danger|attn/);
    expect(dialog.querySelector('[data-agent-steps]')).toBeNull();
    expect(dialog.querySelector('[data-tool-calls]')).toHaveTextContent(/Tool calls\s*0/);
    const go = within(refusal).getByRole('button', { name: 'Go to the decision panel' });
    await waitFor(() => expect(go).toBeEnabled());
    await userEvent.click(go);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    await waitFor(() => expect(onRefSelect).toHaveBeenCalled());
    expect(onRefSelect.mock.calls.at(-1)?.[0]).toMatchObject({ kind: expect.stringMatching(/^(decision|return)$/) });
    expectCleanScan(document.body);
  });

  it('503 llm_unavailable: the model is unavailable, the decision is unaffected, with the request id', async () => {
    vi.spyOn(api, 'askAgent').mockImplementation(async function* () {
      yield* [];
      throw new ApiError({ status: 503, code: 'llm_unavailable', message: 'The language model is off', requestId: 'mock-req-test' });
    });
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    expect(panel().dataset.agentStream).toBe('error');
    expect(dialog).toHaveTextContent('The language model is unavailable.');
    expect(dialog).toHaveTextContent('The decision is unaffected');
    expect(dialog.querySelector('[data-request-id] [data-id]')?.textContent).toMatch(/^mock-req-/);
    expect(within(dialog).getByRole('button', { name: /Retry in/ })).toBeDisabled();
    expectCleanScan(document.body);
  });

  it('an error event in the stream shows the same notice', async () => {
    act(() => setScenario('agent_error'));
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    expect(dialog.querySelector('[data-agent-problem="llm_unavailable"]')).not.toBeNull();
    expect(dialog).toHaveTextContent('UI fixture: the language model stopped answering');
    expect(dialog.querySelector('[data-agent-issues]')).toBeNull();
  });

  it('429 rate_limited: questions wait the seconds of Retry-After, counted down on screen', async () => {
    act(() => setScenario('rate_limited'));
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const problem = dialog.querySelector('[data-agent-problem="rate_limited"]') as HTMLElement;
    expect(problem).toHaveTextContent('Too many questions in a short time.');
    // The mock's 429 carries Retry-After from the message ("in a minute": 60 s).
    expect(problem.querySelector('[data-retry-after]')).toHaveAttribute('data-retry-after', '60');
    expect(problem).toHaveTextContent('The service asks to wait 60 s before the next question.');
    expect(dialog.querySelector('[data-cooldown]')).toHaveTextContent(/New questions resume in (60|59) s\./);
    expect(within(dialog).getByRole('textbox')).toBeDisabled();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Suggested questions' }));
    expect(within(dialog).getByRole('button', { name: ROSSI_Q })).toBeDisabled();
    expectCleanScan(document.body);
  });

  it('403 forbidden_role: says why and offers the other personas', async () => {
    vi.spyOn(api, 'askAgent').mockImplementation(async function* () {
      yield* [];
      throw new ApiError({ status: 403, code: 'forbidden_role', message: 'sara (operator) cannot ask', requestId: 'req-test' });
    });
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    expect(dialog.querySelector('[data-agent-problem="forbidden_role"]')).toHaveTextContent("can't ask the case assistant");
  });

  it('a warehouse persona cannot ask: disabled with the reason', async () => {
    act(() => setPersona('ahmed'));
    const { dialog } = renderAssistant();
    expect(within(dialog).getByRole('textbox')).toBeDisabled();
    expect(await within(dialog).findByRole('button', { name: LINK_Q })).toBeDisabled();
    expect(dialog).toHaveTextContent('This action is for operators and managers (warehouse can view).');
  });

});

describe('CaseAssistant stream control', () => {
  /** A stream that sends its first event and then waits until it is aborted. */
  function hangingStream() {
    return vi.spyOn(api, 'askAgent').mockImplementation(async function* (_body, options) {
      yield readExample<AgentEvent[]>('agent_stream.marco_link.json')[0] as AgentEvent;
      await new Promise<never>((_, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
    });
  }

  it('closing the drawer cancels the stream; the history is kept when it reopens', async () => {
    hangingStream();
    function Host() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            reopen
          </button>
          <CaseAssistant returnId={RET} open={open} onOpenChange={setOpen} />
        </>
      );
    }
    renderWithProviders(<Host />);
    await userEvent.click(await screen.findByRole('button', { name: LINK_Q }));
    await waitFor(() => expect(document.querySelector('[data-step-status="running"]')).not.toBeNull());
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'reopen' }));
    await waitFor(() => expect(panel().dataset.agentStream).toBe('stopped'));
    expect(screen.getByText(AGENT.turn.stopped)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveTextContent(LINK_Q);
  });

  it('Stop cancels the answer being streamed', async () => {
    hangingStream();
    renderAssistant();
    await userEvent.click(await screen.findByRole('button', { name: LINK_Q }));
    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(panel().dataset.agentStream).toBe('stopped'));
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
  });

  it.each([
    ['step_limit', 'The assistant reached its step limit.', 'It reads at most 5 times per question.'],
    ['out_of_scope', 'This question is outside this case.', 'from its read-only tools'],
  ] as const)('a %s refusal gets its own neutral copy, without the decision button', async (reason, title, body) => {
    const done = readExample<AgentEvent[]>('agent_stream.write_refused.json')[1] as AgentEvent;
    vi.spyOn(api, 'askAgent').mockImplementation(async function* () {
      yield { type: 'refusal', seq: 0, reason, text: 'I cannot answer that from this case.' };
      yield done;
    });
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const refusal = dialog.querySelector(`[data-agent-refusal="${reason}"]`) as HTMLElement;
    expect(refusal).toHaveTextContent(title);
    expect(refusal).toHaveTextContent(body);
    expect(within(refusal).queryByRole('button', { name: 'Go to the decision panel' })).toBeNull();
    expectCleanScan(document.body);
  });

  it('a failed tool call reads as failed, in words', async () => {
    const events = readExample<AgentEvent[]>('agent_stream.marco_link.json').map((e) =>
      e.type === 'tool_result' && e.step === 2 ? { ...e, ok: false, summary: 'Orders could not be read', refs: [] } : e,
    );
    vi.spyOn(api, 'askAgent').mockImplementation(async function* () {
      yield* events;
    });
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const failed = dialog.querySelector('[data-step-status="failed"]') as HTMLElement;
    expect(failed).toHaveTextContent('Failed');
    expect(failed).toHaveTextContent('Orders could not be read');
  });

  it('a stream that breaks the contract is shown as received, with the deviations listed', async () => {
    const events = readExample<AgentEvent[]>('agent_stream.marco_link.json').map((e) => (e.type === 'done' ? { ...e, steps_used: 4 } : e));
    vi.spyOn(api, 'askAgent').mockImplementation(async function* () {
      yield* events;
    });
    const { dialog } = renderAssistant();
    await userEvent.click(await within(dialog).findByRole('button', { name: LINK_Q }));
    await settled();
    const issues = dialog.querySelector('[data-agent-issues]') as HTMLElement;
    expect(issues).toHaveAttribute('data-agent-issues', 'steps_used_mismatch');
    expect(issues).toHaveTextContent('The step count reported by the service differs from the steps received.');
    expect(dialog.querySelector('[data-generated]')).toHaveTextContent('A link is a shared identifier');
  });
});

describe('agent harness /dev/agent', () => {
  it('without ?return= opens the demo Marco case by its id from the demo cases', async () => {
    renderApp('/dev/agent');
    expect(await screen.findByRole('heading', { level: 1, name: 'Agent harness' })).toBeInTheDocument();
    // demo_cases.json names RET-000102 for marco.
    expect(await screen.findByRole('heading', { level: 2, name: /Ask about RET-000102/ })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: LINK_Q })).toBeEnabled();
  });


  it('renders the panel as a page and logs the refs it asks the case card to focus', async () => {
    renderApp('/dev/agent?return=RET-000102');
    expect(await screen.findByRole('heading', { level: 1, name: 'Agent harness' })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: LINK_Q }));
    await settled();
    const chip = screen.getAllByRole('link', { name: 'Show cluster CL-000417 on the case card' })[0] as HTMLElement;
    fireEvent.click(chip, { button: 0 });
    const log = document.querySelector('[data-focus-log]') as HTMLElement;
    expect(log).toHaveTextContent('CL-000417');
    expect(within(log).getByRole('link', { name: 'Open on the case card' })).toHaveAttribute('href', '/returns/RET-000102?focus=linked');
    expect(document.querySelector('[data-agent-events]')?.querySelectorAll('li')).toHaveLength(11);
    expectCleanScan(document.body);
  });
});

// Last in the file: answers of the cached scenario still in flight when it ends would mark the
// mode store cached for the next test (the mode is a module-level store).
describe('CaseAssistant in the cached demo', () => {
  it('the cached demo: the assistant is unavailable up front (the agent answers 503), no question is sent', async () => {
    act(() => setScenario('cached'));
    const ask = vi.spyOn(api, 'askAgent');
    const { dialog } = renderAssistant();
    await waitFor(() => expect(dialog.querySelector('[data-agent-unavailable="cached"]')).not.toBeNull());
    // The card has loaded (its link question is there): no cached answer is still in flight to
    // mark the next test's responses as cached.
    expect(await within(dialog).findByRole('button', { name: LINK_Q })).toBeDisabled();
    expect(dialog.querySelector('[data-agent-unavailable]')).toHaveTextContent('Unavailable in the cached demo');
    expect(within(dialog).getByRole('textbox')).toBeDisabled();
    const suggested = within(dialog).getAllByRole('button').filter((b) => b.hasAttribute('data-suggested'));
    expect(suggested.length).toBeGreaterThan(0);
    for (const button of suggested) expect(button).toBeDisabled();
    expect(dialog.querySelector('[data-cached-note]')).toHaveTextContent('The case assistant needs the live model, which is off in the cached demo.');
    expect(dialog).not.toHaveTextContent('MI300X');
    fireEvent.click(suggested[0] as HTMLElement);
    expect(ask).not.toHaveBeenCalled();
    expectCleanScan(document.body);
  });
});
