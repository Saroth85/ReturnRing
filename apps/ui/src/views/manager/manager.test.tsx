// The manager screens on the contract examples (docs/ui-design.md 8, US-601..607, US-208): what each
// screen must show, the actions a person takes, and the rules behind them (rules 1, 5, 9, 11).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { CatalogTask, DemoCasesResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

async function loaded() {
  // The first render compiles the lazily loaded page: allow for a busy machine.
  await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull());
}

function cleanScreen() {
  expect(findUnprovenancedNumbers(document.body), JSON.stringify(findUnprovenancedNumbers(document.body))).toEqual([]);
  expect(findBannedWording(document.body).errors).toEqual([]);
  expect(document.querySelector('[data-error-state]')).toBeNull();
}

afterEach(() => {
  vi.restoreAllMocks();
  act(() => {
    setPersona('sara');
    setScenario('default');
  });
});

const cases = readExample<DemoCasesResponse>('demo_cases.json');

describe('/portfolio (US-601)', () => {
  it('shows counts and euros per quadrant, the value legend and the not-placed customers', async () => {
    renderApp('/portfolio');
    await loaded();
    const q4 = await screen.findByText((_, el) => el?.getAttribute('data-quadrant-tile') === 'Q4');
    expect(within(q4).getByRole('button', { name: /^10/ })).toBeInTheDocument();
    expect(within(q4).getByRole('button', { name: /€344\.85/ })).toBeInTheDocument();
    expect(document.querySelector('[data-value-legend]')?.textContent).toBe(
      'Customer value orients benefits and service. It is never a signal of abuse and never feeds the risk score.',
    );
    expect(document.querySelector('[data-not-placed]')?.textContent).toContain('not enough evidence');
    expect(document.querySelectorAll('[data-quadrant-tile]')).toHaveLength(4);
    for (const tile of document.querySelectorAll('[data-quadrant-tile]')) expect(tile.className).not.toMatch(/attn|danger|ok-|accent/);
    cleanScreen();
  });

  it('opens the customer list of a quadrant on click', async () => {
    const { router } = renderApp('/portfolio');
    await loaded();
    await waitFor(() => expect(document.querySelector('[data-open-quadrant="Q4"]')).not.toBeNull());
    fireEvent.click(document.querySelector('[data-open-quadrant="Q4"]') as HTMLElement);
    await waitFor(() => expect(router.state.location.search).toBe('?quadrant=Q4'));
    expect(await screen.findByRole('dialog', { name: 'Q4 customers' })).toBeInTheDocument();
  });

  it('lists the Q4 customers from ?quadrant=Q4, sortable, with the linked accounts', async () => {
    renderApp('/portfolio?quadrant=Q4');
    await loaded();
    const drawer = await screen.findByRole('dialog', { name: 'Q4 customers' });
    const rows = drawer.querySelectorAll('[data-customer]');
    expect(rows).toHaveLength(10);
    expect(drawer.querySelector('[data-customer="C-MARCO-1"]')).not.toBeNull();
    fireEvent.click(within(drawer).getByRole('button', { name: 'Sort by Value' }));
    expect(drawer.querySelector('[data-customer]')?.getAttribute('data-customer')).toBe('C-MARCO-7');
    expect(findUnprovenancedNumbers(drawer)).toEqual([]);
  });

  it('annotates the golden customers the demo cases name, and none before a reset', async () => {
    renderApp('/portfolio');
    await loaded();
    const note = await waitFor(() => {
      const el = document.querySelector('[data-demo-note]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(note.textContent).toContain('Giulia: same return rate as Marco');
    expect(note.textContent).toContain('Elena: her open return follows the guarantee path');
  });

  it('follows the demo cases of the API, not ids of its own: a store without Elena annotates Giulia only', async () => {
    vi.spyOn(api, 'demoCases').mockResolvedValue({ ...cases, cases: cases.cases.filter((c) => c.key !== 'elena') });
    renderApp('/portfolio');
    await loaded();
    const note = await waitFor(() => {
      const el = document.querySelector('[data-demo-note]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(note.textContent).toContain('Giulia');
    expect(note.textContent).not.toContain('Elena');
  });

  it('annotates nothing before a demo reset (404), and the matrix still renders', async () => {
    act(() => setScenario('empty'));
    renderApp('/portfolio');
    await loaded();
    await waitFor(() => expect(document.querySelectorAll('[data-quadrant-tile]')).toHaveLength(4));
    expect(document.querySelector('[data-demo-note]')).toBeNull();
    expect(document.querySelector('[data-error-state]')).toBeNull();
  });

  it('switches to the table view', async () => {
    renderApp('/portfolio?view=table');
    await loaded();
    const table = await screen.findByText((_, el) => el?.hasAttribute('data-quadrant-table') ?? false);
    expect(within(table).getAllByRole('row')).toHaveLength(6);
    cleanScreen();
  });
});

describe('/simulator (US-602, ADR-04)', () => {
  it('shows the server net benefit of assumptions-v1 with the baseline and the period', async () => {
    renderApp('/simulator');
    await loaded();
    const shown = await screen.findByText((_, el) => el?.getAttribute('data-shown-scenario') === 'base');
    expect(shown).toHaveAttribute('data-source', 'server');
    expect(within(shown.querySelector('[data-net-total]') as HTMLElement).getByRole('button', { name: /\+€368\.00/ })).toBeInTheDocument();
    expect(screen.getByText('Navigli base policy (spec 4) on the same period')).toBeInTheDocument();
    expect(screen.getAllByText('month, 1,000 return requests (illustrative)').length).toBeGreaterThan(0);
    expect(screen.getByText(/Not observed yet: realised benefit needs observed outcomes/)).toBeInTheDocument();
    expect(screen.getAllByText('The refund owed to the customer is never counted as a saving.').length).toBeGreaterThan(0);
    cleanScreen();
  });

  it('shows the four scenarios and morphs to "Both": −€192, negative in words', async () => {
    const { router } = renderApp('/simulator');
    await loaded();
    const scenarios = await screen.findByRole('list', { name: 'Sensitivity scenarios' });
    expect(within(scenarios).getByRole('button', { name: /\+€48\.00/ })).toBeInTheDocument();
    expect(within(scenarios).getByRole('button', { name: /\+€128\.00/ })).toBeInTheDocument();
    fireEvent.click(within(scenarios).getByRole('button', { name: 'Both' }));
    await waitFor(() => expect(router.state.location.search).toBe('?scenario=both'));
    const shown = document.querySelector('[data-shown-scenario="both"]') as HTMLElement;
    expect(within(shown).getByText('Net benefit is negative under these assumptions')).toBeInTheDocument();
    expect(within(shown.querySelector('[data-net-total]') as HTMLElement).getByRole('button', { name: /−€192\.00/ })).toBeInTheDocument();
    expect(document.querySelector('[data-assumption-label="assumptions-v1/both"]')).not.toBeNull();
  });

  it('recomputes an edit in the browser, simulated, with its own origin', async () => {
    renderApp('/simulator');
    await loaded();
    const field = await screen.findByLabelText(/Logistics cost per return/);
    fireEvent.change(field, { target: { value: '6.5' } });
    expect(await screen.findByText('Unsaved scenario · computed in your browser · simulated')).toBeInTheDocument();
    const shown = document.querySelector('[data-shown-scenario="base"]') as HTMLElement;
    expect(shown).toHaveAttribute('data-source', 'browser');
    expect(within(shown.querySelector('[data-net-total]') as HTMLElement).getByRole('button', { name: /\+€248\.00/ })).toBeInTheDocument();
    expect(shown.textContent).toContain('Computed in your browser');
    const scenarios = screen.getByRole('list', { name: 'Sensitivity scenarios' });
    expect(within(scenarios).getByRole('button', { name: /−€252\.00/ })).toBeInTheDocument();
    cleanScreen();
  });

  it('lets only a manager save a new version, with a note', async () => {
    renderApp('/simulator');
    await loaded();
    fireEvent.change(await screen.findByLabelText(/Logistics cost per return/), { target: { value: '6.5' } });
    expect(screen.getByRole('button', { name: /Save as/ })).toBeDisabled();
    expect(screen.getByText('This action is for managers (operator can view).')).toBeInTheDocument();
  });

  it('saves as assumptions-v2 for Luca (PUT with base_version, note and author)', async () => {
    act(() => setPersona('luca'));
    renderApp('/simulator');
    await loaded();
    fireEvent.change(await screen.findByLabelText(/Logistics cost per return/), { target: { value: '6.5' } });
    const save = screen.getByRole('button', { name: /Save as/ });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Note for the new version (required)'), { target: { value: 'Carrier contract renegotiated' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(await screen.findByText((_, el) => el?.getAttribute('data-saved-version') === 'assumptions-v2', {})).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('[data-history-version="assumptions-v2"]')).not.toBeNull());
  });

  it('re-buckets the matrix in the threshold sandbox without touching the net benefit', async () => {
    renderApp('/simulator');
    await loaded();
    const risk = await screen.findByLabelText(/Risk threshold/, {});
    fireEvent.change(risk, { target: { value: '0.5' } });
    const q4 = document.querySelector('[data-sandbox-row="Q4"]') as HTMLElement;
    const cells = within(q4).getAllByRole('cell');
    expect(Number(cells[1]?.textContent)).toBeGreaterThanOrEqual(Number(cells[0]?.textContent));
    expect(screen.getByRole('button', { name: 'Reset to the engine thresholds' })).toBeInTheDocument();
    const shown = document.querySelector('[data-shown-scenario="base"]') as HTMLElement;
    expect(shown).toHaveAttribute('data-source', 'server');
  });
});

describe('/kpi (US-603)', () => {
  it('shows the simulated net benefit, realised not observed, decisions, time, abstentions and false positives', async () => {
    renderApp('/kpi?week=2026-09-21');
    await loaded();
    const hero = await screen.findByText((_, el) => el?.hasAttribute('data-kpi-net-benefit') ?? false);
    expect(within(hero).getByRole('button', { name: /\+€140\.83/ })).toBeInTheDocument();
    expect(screen.getByText('Not observed yet: realised benefit needs observed outcomes')).toBeInTheDocument();
    expect(document.querySelector('[data-assumption-label="kpi/2026-09-21"]')).not.toBeNull();
    expect(screen.getByText('eval-2026-09-30')).toBeInTheDocument();
    expect(screen.getByText('Labels come from the offline evaluation; the running API never reads ground truth.')).toBeInTheDocument();
    expect(document.querySelectorAll('[data-dot-row]')).toHaveLength(5);
    cleanScreen();
  });

  it('moves to the previous week by URL', async () => {
    const { router } = renderApp('/kpi?week=2026-09-21');
    await loaded();
    fireEvent.click(await screen.findByRole('button', { name: 'Previous week' }));
    await waitFor(() => expect(router.state.location.search).toBe('?week=2026-09-14'));
  });
});

describe('offline report not in the snapshot (404 report_unavailable)', () => {
  it.each(['/kpi?week=2026-09-21', '/fairness'])('%s shows an empty state with the API reason, no error and no retry', async (route) => {
    act(() => {
      setPersona('luca');
      setScenario('no_report');
    });
    renderApp(route);
    await loaded();
    const empty = await waitFor(() => {
      const el = document.querySelector('[data-report-unavailable]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(empty.textContent).toContain('Offline report not in the snapshot');
    expect(empty.querySelector('[data-verbatim]')?.textContent).toContain('the offline evaluation report (FAIR-02) is not in the serving snapshot');
    expect(document.querySelector('[data-error-state]')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
  });
});

describe('/fairness (US-605)', () => {
  it('warns on the little-data gap only, as the model’s errors, never a group', async () => {
    renderApp('/fairness');
    await loaded();
    await screen.findByText((_, el) => el?.hasAttribute('data-fairness-banner') ?? false);
    const warned = Array.from(document.querySelectorAll('[data-warning]')).map((d) => d.getAttribute('data-fairness-dimension'));
    expect(warned).toEqual(['low_data']);
    expect(document.querySelectorAll('[data-fairness-dimension]')).toHaveLength(6);
    expect(document.querySelector('[data-fairness-banner]')?.textContent).toContain('It flags the model’s errors, never a group.');
    expect(screen.getByText(/Gender, country, age, names and raw identifiers are not model inputs/)).toBeInTheDocument();
    cleanScreen();
  });
});

describe('/products (US-208, US-606)', () => {
  it('lists the anomalies with observed against expected and filters by status', async () => {
    const { router } = renderApp('/products');
    await loaded();
    await screen.findByText((_, el) => el?.hasAttribute('data-anomalies') ?? false);
    expect(document.querySelectorAll('[data-anomaly]')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Open' }));
    await waitFor(() => expect(router.state.location.search).toBe('?status=open'));
    expect(document.querySelectorAll('[data-anomaly]')).toHaveLength(2);
    cleanScreen();
  });

  it("shows Elena's jacket: z = 6.86, the regular returns and the open task, no restriction", async () => {
    renderApp('/products/V-JKT-DEF~defective~2026-09-21');
    await loaded();
    const summary = await screen.findByText((_, el) => el?.hasAttribute('data-anomaly-summary') ?? false);
    expect(within(summary).getByRole('button', { name: /z = 6\.86/ })).toBeInTheDocument();
    expect(screen.getByText(/These returns stay regular; defect returns follow the guarantee path. No restriction applies to these customers./)).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('[data-task="CT-000012"]')).not.toBeNull());
    cleanScreen();
  });

  it('opens a catalogue task on an open anomaly (POST, 201)', async () => {
    act(() => setPersona('luca'));
    renderApp('/products/V-SNK-2290~defective~2026-09-21');
    await loaded();
    const form = await screen.findByText((_, el) => el?.hasAttribute('data-catalog-task-form') ?? false, {});
    fireEvent.change(within(form).getByLabelText('Note (required)'), { target: { value: 'Check the sole glue of the batch' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Open the task' }));
    expect(await screen.findByText(/Task CT-000012 opened\./, {})).toBeInTheDocument();
  });

  it('offers the queued returns of the anomaly list, and says when there are none', async () => {
    act(() => setPersona('luca'));
    renderApp('/products/V-SNK-2290~defective~2026-09-21');
    await loaded();
    const form = await screen.findByText((_, el) => el?.hasAttribute('data-catalog-task-form') ?? false, {});
    // product_anomalies.json: the week's returns are not in the queue (queued_return_ids is empty).
    expect(form.querySelector('[data-task-returns]')).toBeNull();
    expect(form.querySelector('[data-no-queued-returns]')).not.toBeNull();
  });

  it("opened from Elena's card, offers her return (the card's queued_return_ids) and sends it", async () => {
    act(() => setPersona('luca'));
    // Live, the new task enters the task list: its card replaces the form, and the page still says it opened.
    const task = readExample<CatalogTask>('catalog_task.elena.json');
    const tasks = vi.spyOn(api, 'listCatalogTasks').mockResolvedValue({ items: [] as CatalogTask[] });
    const create = vi.spyOn(api, 'createCatalogTask').mockImplementation(async () => {
      tasks.mockResolvedValue({ items: [task] });
      return task;
    });
    const elena = cases.cases.find((c) => c.key === 'elena')!.return_id;
    renderApp(`/products/V-JKT-DEF~defective~2026-09-21?return=${elena}`);
    await loaded();
    const select = await waitFor(() => {
      const el = document.querySelector('[data-task-returns]');
      expect(el).not.toBeNull();
      return el as HTMLSelectElement;
    });
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['', elena]);
    expect(select.value).toBe(elena);
    const form = document.querySelector('[data-catalog-task-form]') as HTMLElement;
    fireEvent.change(within(form).getByLabelText('Note (required)'), { target: { value: 'Check the sleeve length of the batch' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Open the task' }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create.mock.calls[0]?.[0]).toMatchObject({ return_id: elena, author: 'luca' });
    await waitFor(() => expect(document.querySelector(`[data-task="${task.task_id}"]`)).not.toBeNull());
    expect(document.querySelector('[data-task-created]')?.textContent).toBe(`Task ${task.task_id} opened.`);
  });

  it('moves a task forward and shows the follow-up of a done one with its caveat', async () => {
    act(() => setPersona('luca'));
    renderApp('/products/tasks');
    await loaded();
    const open = await screen.findByText((_, el) => el?.getAttribute('data-task') === 'CT-000012', {});
    fireEvent.click(within(open).getByRole('button', { name: 'Start' }));
    expect(await within(open).findByText('Task CT-000012: In progress.', {})).toBeInTheDocument();
    const done = document.querySelector('[data-task="CT-000007"]') as HTMLElement;
    expect(within(done).getByText('A decrease alone does not prove the effect of the correction: that needs a controlled comparison.')).toBeInTheDocument();
    cleanScreen();
  });
});

describe('/reviews (US-607)', () => {
  it('lists the lapsed restriction from the server field, with its approver and the review link', async () => {
    renderApp('/reviews');
    await loaded();
    const row = await screen.findByText((_, el) => el?.getAttribute('data-review-row') === 'RET-000088', {});
    expect(row.querySelector('[data-lapsed]')?.textContent).toContain('Lapsed on 27 Sep 2026');
    // Renew exists since contract 1.5.0 (HAS_RENEW): the review renews or lifts.
    expect(within(row).getByRole('link', { name: 'Review: lift or renew…' })).toHaveAttribute('href', '/returns/RET-000088?action=review');
    expect(row.querySelector('[data-lapsed]')?.textContent).toContain('Renew or lift.');
    await waitFor(() => expect(within(row).getByText('luca')).toBeInTheDocument());
    expect(screen.getByText('Refund within the legal terms')).toBeInTheDocument();
    expect(document.querySelector('[data-coupon-applications]')).not.toBeNull();
    cleanScreen();
  });

  it('on the read-only public demo, says the review and the reversal are not available here', async () => {
    act(() => setScenario('read_only'));
    renderApp('/reviews');
    await loaded();
    const row = await screen.findByText((_, el) => el?.getAttribute('data-review-row') === 'RET-000088', {});
    await waitFor(() => expect(row.querySelector('[data-role-reason]')).not.toBeNull());
    expect(row.querySelector('[data-role-reason]')?.textContent).toContain('Read-only public demo');
    // The reversal stays on screen, disabled with the reason (never hidden, docs/ui-design.md 5.8).
    const reverse = await screen.findAllByRole('button', { name: /^Reverse/ });
    await waitFor(() => reverse.forEach((button) => expect(button).toBeDisabled()));
    expect(reverse[0]?.closest('[data-role-gate]')?.querySelector('[data-role-reason]')?.textContent).toBe('Read-only public demo');
  });
});

describe('read-only public demo (PUBLIC_READ_ONLY, /readyz public_read_only)', () => {
  it('disables the manager writes with the reason, never hides them', async () => {
    act(() => {
      setPersona('luca');
      setScenario('read_only');
    });
    renderApp('/simulator');
    await loaded();
    fireEvent.change(await screen.findByLabelText(/Logistics cost per return/), { target: { value: '6.5' } });
    fireEvent.change(screen.getByLabelText('Note for the new version (required)'), { target: { value: 'Carrier contract renegotiated' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /Save as/ })).toBeDisabled());
    expect(screen.getByText('Read-only public demo')).toBeInTheDocument();
  });

  it('disables the catalogue task moves and says why', async () => {
    act(() => {
      setPersona('luca');
      setScenario('read_only');
    });
    renderApp('/products/tasks');
    await loaded();
    const open = await screen.findByText((_, el) => el?.getAttribute('data-task') === 'CT-000012', {});
    await waitFor(() => expect(within(open).getByRole('button', { name: 'Start' })).toBeDisabled());
    expect(within(open).getByText('Read-only public demo')).toBeInTheDocument();
  });
});

describe('/audit (US-604)', () => {
  it('lists the entries with the human approval pill, and expands a row into what changed', async () => {
    renderApp('/audit');
    await loaded();
    // Marco's approval (entry 38): the restriction with its review date, approved by a person.
    const approval = await screen.findByText((_, el) => el?.getAttribute('data-audit-row') === '38', {});
    expect(within(approval).getByText('27 Dec 2026')).toBeInTheDocument();
    fireEvent.click(within(approval).getByRole('button', { name: /Details/ }));
    const details = document.querySelector('[data-audit-details="38"]') as HTMLElement;
    expect(within(details).getAllByText('Coupons paused for the linked accounts')).toHaveLength(2);
    expect(within(details).getByText('policy-2026-10-12')).toBeInTheDocument();
    cleanScreen();
  });

  it('filters by actor and human actions, in the URL', async () => {
    renderApp('/audit?actor=luca');
    await loaded();
    await screen.findByText((_, el) => el?.hasAttribute('data-audit-table') ?? false, {});
    const actors = Array.from(document.querySelectorAll('[data-audit-row] [data-actor]')).map((a) => a.getAttribute('data-actor'));
    expect(new Set(actors)).toEqual(new Set(['luca']));
  });

  it('exports the CSV named after the filters', async () => {
    const created = vi.fn(() => 'blob:mock');
    const revoked = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: created, configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revoked, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderApp('/audit?return_id=RET-000102&human=1');
    await loaded();
    fireEvent.click(await screen.findByRole('button', { name: 'Export CSV' }));
    const done = await screen.findByText((_, el) => el?.hasAttribute('data-exported') ?? false, {});
    expect(done.getAttribute('data-exported')).toBe('returnring-audit-RET-000102-human.csv');
    expect(click).toHaveBeenCalled();
    const blob = (created.mock.calls[0] as unknown as [Blob])[0];
    const text = await blob.text();
    expect(text.split('\n')[0]).toContain('actor_role');
    expect(text).not.toMatch(/,system,/);
  });
});
