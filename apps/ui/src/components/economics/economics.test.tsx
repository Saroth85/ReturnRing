// Economics components (docs/ui-design.md 5.6): the net benefit with disjoint components, the
// refund never counted as a saving (rule 9), labels of assumption sets, and provenance (US-906).
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { KpiResponse, SimulatorAssumptionsResponse } from '@/api/models';
import { netBenefit, toView } from '@/sim';
import { readExample } from '@/test/examples';
import { renderWithProviders } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import { AssumptionLabel, NetBenefitBreakdown, parseAssumptionLabel, Waterfall } from './index';

const v1 = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v1.json').current;
const kpi = readExample<KpiResponse>('kpi.json');

describe('parseAssumptionLabel', () => {
  it.each([
    ['DEC-000102-1/proposal', { kind: 'decision', decisionId: 'DEC-000102-1', alternative: 'proposal', returnId: 'RET-000102' }],
    ['kpi/2026-09-21', { kind: 'kpi', week: '2026-09-21' }],
    ['assumptions-v1/both', { kind: 'scenario', version: 'assumptions-v1', scenario: 'both' }],
    ['assumptions-v1/logistics_halved', { kind: 'scenario', version: 'assumptions-v1', scenario: 'logistics_halved' }],
    ['assumptions-v2', { kind: 'stored', version: 'assumptions-v2' }],
    ['something/else/entirely', { kind: 'raw', label: 'something/else/entirely' }],
  ])('%s', (label, parsed) => {
    expect(parseAssumptionLabel(label)).toEqual(parsed);
  });
});

describe('AssumptionLabel', () => {
  it('reads a scenario with the stored version it is based on', () => {
    renderWithProviders(<AssumptionLabel label="assumptions-v1/both" basedOn="assumptions-v1" />);
    expect(screen.getByText(/Scenario: logistics halved and abandonment doubled/)).toBeInTheDocument();
    expect(screen.getByText('assumptions-v1')).toHaveAttribute('data-id');
  });

  it('reads a decision alternative with a link to the decision record', () => {
    renderWithProviders(<AssumptionLabel label="DEC-000102-1/current_terms" basedOn="assumptions-v1" />);
    const link = screen.getByRole('link', { name: 'DEC-000102-1' });
    expect(link).toHaveAttribute('href', '/returns/RET-000102/versions/DEC-000102-1');
    expect(screen.getByText(/alternative: current terms/)).toBeInTheDocument();
  });

  it('reads a KPI week with its date', () => {
    renderWithProviders(<AssumptionLabel label="kpi/2026-09-21" basedOn="assumptions-v1" />);
    expect(screen.getByText('21 Sep 2026')).toBeInTheDocument();
  });

  it('shows an unknown pattern raw', () => {
    renderWithProviders(<AssumptionLabel label="custom label" basedOn={null} />);
    expect(screen.getByText('custom label')).toBeInTheDocument();
  });
});

describe('NetBenefitBreakdown', () => {
  it('lists the eight components in contract order, signed, then the total and the rule-9 footnote', () => {
    const { container } = renderWithProviders(<NetBenefitBreakdown view={v1.net_benefit} variant="list" />);
    const rows = Array.from(container.querySelectorAll('[data-component]')).map((r) => r.getAttribute('data-component'));
    expect(rows).toEqual([
      'logistics_costs_avoided',
      'undue_discounts_avoided',
      'time_saved_value',
      'incremental_margin',
      'incentives',
      'system_cost',
      'error_cost',
      'abandonment_margin_lost',
    ]);
    expect(screen.getByRole('button', { name: /\+€640\.00/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /−€240\.00/ })).toBeInTheDocument();
    const total = container.querySelector('[data-net-total]') as HTMLElement;
    expect(within(total).getByRole('button', { name: /\+€368\.00/ })).toBeInTheDocument();
    expect(screen.getByText('The refund owed to the customer is never counted as a saving.')).toBeInTheDocument();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
    expect(findBannedWording(container).errors).toEqual([]);
  });

  it('shows the KPI week net benefit with its components', () => {
    const { container } = renderWithProviders(<NetBenefitBreakdown view={kpi.net_benefit_simulated} variant="list" />);
    expect(within(container.querySelector('[data-net-total]') as HTMLElement).getByRole('button', { name: /\+€140\.83/ })).toBeInTheDocument();
  });
});

describe('Waterfall', () => {
  it('draws increases solid and decreases hatched, with a visible zero line', () => {
    const { container } = renderWithProviders(<Waterfall view={v1.net_benefit} />);
    expect(container.querySelectorAll('[data-bar="increase"]')).toHaveLength(3);
    expect(container.querySelectorAll('[data-bar="decrease"]')).toHaveLength(4);
    expect(container.querySelectorAll('[data-bar="zero"]')).toHaveLength(1);
    expect(container.querySelector('[data-bar="total"]')).not.toBeNull();
    expect(container.querySelector('[data-zero-line]')).not.toBeNull();
    for (const bar of container.querySelectorAll('[data-bar="decrease"]')) expect(bar.className).toContain('hatch-sim');
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('says a negative total in words, hatched, never in the danger colour', () => {
    const both = v1.sensitivity.find((s) => s.id === 'both')!.net_benefit;
    const { container } = renderWithProviders(<Waterfall view={both} />);
    expect(screen.getByText('Net benefit is negative under these assumptions')).toBeInTheDocument();
    expect(within(container.querySelector('[data-net-total]') as HTMLElement).getByRole('button', { name: /−€192\.00/ })).toBeInTheDocument();
    const total = container.querySelector('[data-bar="total"]') as HTMLElement;
    expect(total.className).toContain('hatch-sim');
    expect(container.innerHTML).not.toMatch(/danger/);
  });

  it('renders a browser-computed view with its own provenance', () => {
    const provenance = { origin: 'Computed in your browser', period_start: '2026-10-01', period_end: '2026-10-31', nature: 'simulated' as const };
    const inputs = { ...v1.net_benefit.assumptions, incentives_eur: 1000 };
    const view = toView(inputs, { version: 'unsaved', basis: 'simulated', based_on: 'assumptions-v1' }, netBenefit(inputs), provenance);
    render(<Waterfall view={view} />);
    expect(screen.getByRole('button', { name: /−€432\.00/ })).toBeInTheDocument();
  });
});
