// Provenance on numbers (rule 11, US-906): every Figure carries origin, period and nature.
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Metric, Provenance } from '@/api/models';
import { Figure, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { provenanceModeStore } from '@/components/provenance/stores';
import { findUnprovenancedNumbers } from '@/test/scan';
import { readExample } from '@/test/examples';
import type { CaseCard } from '@/api/models';

const synthetic: Provenance = {
  origin: 'Contract example: risk model risk-xgb-0.1.0 on decision-time features of the synthetic shop',
  period_start: '2025-10-01',
  period_end: '2026-09-30',
  nature: 'synthetic',
};
const simulated: Provenance = { ...synthetic, origin: 'Contract example: simulator assumptions-v1', nature: 'simulated' };
const risk: Metric = { value: 0.87, unit: 'probability', provenance: synthetic };
const threshold: Metric = { value: 0.6, unit: 'probability', provenance: synthetic };

describe('Figure', () => {
  it('renders the value with its nature glyph, origin, period and nature', () => {
    const { container } = render(<Figure metric={risk} label="Risk score" />);
    const figure = container.querySelector('[data-figure]');
    expect(figure).not.toBeNull();
    expect(figure).toHaveAttribute('data-nature', 'synthetic');
    expect(screen.getByRole('button', { name: /0\.87/ })).toBeInTheDocument();
    expect(container.querySelector('[data-nature-mark="synthetic"]')).not.toBeNull();
    const button = screen.getByRole('button', { name: /0\.87/ });
    const description = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(description?.textContent).toContain('Synthetic');
    expect(description?.textContent).toContain(synthetic.origin);
    expect(description?.textContent).toContain('1 Oct 2025 – 30 Sep 2026');
  });

  it('prints a threshold next to the value', () => {
    render(<Figure metric={risk} compareTo={threshold} />);
    expect(screen.getByRole('button', { name: /0\.87.*threshold 0\.60/ })).toBeInTheDocument();
  });

  it('opens the provenance popover with the origin verbatim', async () => {
    render(<Figure metric={risk} label="Risk score" />);
    fireEvent.click(screen.getByRole('button', { name: /0\.87/ }));
    const detail = await screen.findByText(synthetic.origin, { selector: 'dd' });
    expect(detail.textContent?.startsWith('Contract example:')).toBe(true);
    expect(screen.getByRole('link', { name: 'What do natures mean?' })).toHaveAttribute('href', '/about#natures');
  });

  it('shows the nature word when it differs from the section scope', () => {
    const { container } = render(
      <ProvenanceScope provenance={synthetic} covers="the case">
        <Figure metric={{ value: 123.5, unit: 'eur', provenance: simulated }} signed />
        <Figure metric={risk} />
      </ProvenanceScope>,
    );
    const badges = container.querySelectorAll('[data-provenance-badge]');
    expect(badges[0]?.textContent).toContain('Simulated');
    // Same nature as the scope: the glyph only (its word is screen-reader text).
    expect(badges[1]?.querySelector(':scope > span:not([data-nature-mark])')).toBeNull();
  });

  it('prints the nature word and period on every figure in provenance mode', () => {
    act(() => provenanceModeStore.set(true));
    try {
      const { container } = render(<Figure metric={risk} />);
      const badge = container.querySelector('[data-provenance-badge]');
      expect(badge?.textContent).toContain('Synthetic');
      expect(badge?.textContent).toContain('2026');
    } finally {
      act(() => provenanceModeStore.set(false));
    }
  });

  it('marks illustrative origins', () => {
    const { container } = render(
      <Figure metric={{ value: 41, unit: 'seconds', provenance: { ...simulated, origin: 'Illustrative example, not measured' } }} />,
    );
    expect(container.querySelector('[data-figure]')).toHaveAttribute('data-nature', 'illustrative');
  });
});

describe('PlainNumber and ProvenanceScope', () => {
  it('throws outside a provenance scope in dev builds', () => {
    const original = console.error;
    console.error = () => undefined;
    try {
      expect(() => render(<PlainNumber value={99} unit="eur" />)).toThrow(/outside a ProvenanceScope/);
    } finally {
      console.error = original;
    }
  });

  it('renders inside a scope with a footer naming nature, origin and period', () => {
    render(
      <ProvenanceScope provenance={synthetic} covers="orders">
        <PlainNumber value={99} unit="eur" />
        <ProvenanceFooter />
      </ProvenanceScope>,
    );
    expect(screen.getByText('€99.00')).toBeInTheDocument();
    expect(screen.getByText(/Numbers in this section/)).toBeInTheDocument();
    expect(screen.getByText(`· ${synthetic.origin}`)).toBeInTheDocument();
  });
});

describe('provenance scan (PROV-UI)', () => {
  it('flags a bare number and accepts figures, scopes, ids and dates', () => {
    const { container } = render(
      <div>
        <p>Total 42</p>
        <Figure metric={risk} />
        <span data-id>RET-000102</span>
        <time data-date>27 Dec 2026</time>
      </div>,
    );
    const found = findUnprovenancedNumbers(container);
    expect(found.map((f) => f.text)).toEqual(['Total 42']);
  });

  it('every metric of a case card example renders with its provenance', () => {
    const card = readExample<CaseCard>('case_card.marco.json');
    const metrics = [card.risk?.score, card.value?.value_eur, card.orders.return_rate].filter((m): m is Metric => m !== undefined);
    const { container } = render(
      <div>
        {metrics.map((m, i) => (
          <Figure key={i} metric={m} />
        ))}
      </div>,
    );
    expect(container.querySelectorAll('[data-figure]')).toHaveLength(metrics.length);
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });
});
