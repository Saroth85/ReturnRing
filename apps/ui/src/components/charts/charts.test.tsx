// Chart components (docs/ui-design.md 4.8, 5.6): a table view for every chart, the provenance
// footer, neutral dots, and the before/after split of a catalogue correction (US-606).
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FairnessResponse, ProductAnomaliesResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { findUnprovenancedNumbers } from '@/test/scan';
import { ChartFrame, DotPlot, firstWeekAfter, Sparkline, WeeklyTrendChart } from './index';

const anomalies = readExample<ProductAnomaliesResponse>('product_anomalies.json');
const fairness = readExample<FairnessResponse>('fairness.json');
const jacket = anomalies.items[0]!;
const dress = anomalies.items[2]!;

describe('ChartFrame', () => {
  it('toggles to a table and carries the provenance footer', () => {
    const { container } = render(
      <ChartFrame
        title="Returns per week"
        provenance={anomalies.provenance}
        covers="trends"
        table={{ caption: 'Returns per week', columns: ['Week', 'Returns'], rows: [{ key: 'a', cells: ['21 Sep', '11'] }] }}
      >
        <svg data-testid="chart" />
      </ChartFrame>,
    );
    expect(screen.getByTestId('chart')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }));
    expect(screen.queryByTestId('chart')).toBeNull();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View as chart' })).toHaveAttribute('aria-pressed', 'true');
    expect(container.querySelector('[data-provenance-footer]')?.textContent).toContain(anomalies.provenance.origin);
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });
});

describe('WeeklyTrendChart', () => {
  it('lists every week in its table, with numbers covered by the trend provenance', () => {
    const { container } = render(
      <WeeklyTrendChart title="V-JKT-DEF" trend={jacket.trend} provenance={anomalies.provenance} flagged={{ week: jacket.week, expected: jacket.expected, z: jacket.z }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }));
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(jacket.trend.length + 1);
    expect(screen.getByText('23.9%')).toBeInTheDocument();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('splits the weeks before and after a catalogue correction', () => {
    expect(firstWeekAfter(dress.trend, '2026-08-28T16:00:00Z')).toBe('2026-08-31');
    expect(firstWeekAfter(dress.trend, null)).toBeNull();
    render(<WeeklyTrendChart title="V-DRS-0815" trend={dress.trend} provenance={anomalies.provenance} correctionAt="2026-08-28T16:00:00Z" />);
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }));
    expect(screen.getByRole('columnheader', { name: 'Before and after the correction' })).toBeInTheDocument();
    expect(screen.getAllByRole('cell', { name: 'before' })).toHaveLength(5);
    expect(screen.getAllByRole('cell', { name: 'after' })).toHaveLength(1);
  });

  it('says so when a variant has no weekly series', () => {
    render(<WeeklyTrendChart title="V-SNK-2290" trend={[]} provenance={anomalies.provenance} />);
    expect(screen.getByText('No weekly series for this variant yet.')).toBeInTheDocument();
  });
});

describe('DotPlot', () => {
  it('draws one neutral dot per segment and a bracket across the gap', () => {
    const low = fairness.dimensions.find((d) => d.dimension === 'low_data')!;
    const { container } = render(
      <DotPlot
        rows={low.segments.map((s) => ({ key: s.segment, label: s.segment, value: s.false_positive_rate, count: s.customers }))}
        valueLabel="False-positive rate"
        countLabel="Customers"
        bracket={{ text: 'Gap', label: 'gap', attention: low.warning }}
      />,
    );
    const dots = container.querySelectorAll('[data-dot]');
    expect(dots).toHaveLength(2);
    for (const dot of dots) expect(dot.className).not.toMatch(/attn|danger|ok-|accent/);
    expect(container.querySelector('[data-bracket="attention"]')).not.toBeNull();
  });
});

describe('Sparkline', () => {
  it('prints no number of its own and marks the highlighted point', () => {
    const { container } = render(<Sparkline values={jacket.trend.map((w) => w.returns)} label="Weekly returns" highlight={jacket.trend.length - 1} />);
    expect(container.textContent).toBe('');
    expect(container.querySelectorAll('circle')).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'Weekly returns' })).toBeInTheDocument();
  });
});
