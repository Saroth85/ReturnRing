// Chart components (docs/ui-design.md 4.8, 5.6): a table view for every chart, the provenance
// footer, neutral dots, and the before/after split of a catalogue correction (US-606).
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FairnessResponse, ProductAnomaliesResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { findUnprovenancedNumbers } from '@/test/scan';
import { ChartFrame, DotPlot, firstWeekAfter, Sparkline, WEEK_LABEL_PX, WeeklyTrendChart, weekTickIndices, type TrendWeek } from './index';

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

/** A year of weeks, as the live store sends (53 Mondays to 28 Sep 2026); the examples hold 8. */
function yearOfWeeks(): TrendWeek[] {
  const out: TrendWeek[] = [];
  const end = Date.UTC(2026, 8, 28);
  for (let i = 52; i >= 0; i -= 1) {
    const week = new Date(end - i * 7 * 86_400_000).toISOString().slice(0, 10);
    out.push({ week, sales: 40 + (i % 7), returns: i === 4 ? 11 : 2, return_rate: i === 4 ? 0.25 : 0.05 });
  }
  return out;
}

describe('weekTickIndices: week labels that never overlap', () => {
  const minApart = (shown: Set<number>, slot: number) => {
    const sorted = [...shown].sort((a, b) => a - b);
    return Math.min(...sorted.slice(1).map((v, i) => (v - (sorted[i] ?? 0)) * slot));
  };

  it.each([800, 520, 300])('53 weeks on a %i px plot: labels at least a label apart, the flagged week always kept', (plot) => {
    const shown = weekTickIndices(53, plot, 48);
    expect(minApart(shown, plot / 53)).toBeGreaterThanOrEqual(WEEK_LABEL_PX);
    expect(shown.has(48)).toBe(true);
    expect(shown.size).toBeGreaterThan(1);
  });

  it('keeps the last week too when it does not collide with the flagged one', () => {
    expect(weekTickIndices(53, 800, 48).has(52)).toBe(true);
    expect(weekTickIndices(53, 800, 20).has(52)).toBe(true);
    expect(weekTickIndices(53, 300, null).has(52)).toBe(true);
  });

  it('8 weeks on a wide plot: every week labelled, as before', () => {
    expect([...weekTickIndices(8, 800, 5)].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });
});

describe('WeeklyTrendChart', () => {
  it.each([1000, 340])('draws 53 live weeks at %i px with labels that do not collide', (width) => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width, height: 400, x: 0, y: 0, top: 0, left: 0, right: width, bottom: 400, toJSON: () => ({}) });
    const trend = yearOfWeeks();
    const flaggedWeek = trend[48]!.week;
    const { container } = render(
      <WeeklyTrendChart
        title="V-JKT-DEF"
        trend={trend}
        provenance={anomalies.provenance}
        flagged={{ week: flaggedWeek, expected: jacket.expected, z: jacket.z }}
      />,
    );
    const charts = container.querySelectorAll('svg.recharts-surface');
    expect(charts.length).toBe(2);
    for (const svg of charts) {
      const ticks = [...svg.querySelectorAll('[data-week-tick]')];
      expect(ticks.length).toBeGreaterThan(1);
      expect(ticks.length).toBeLessThan(trend.length);
      const xs = ticks.map((t) => Number(t.getAttribute('x'))).sort((a, b) => a - b);
      const gaps = xs.slice(1).map((x, i) => x - (xs[i] ?? 0));
      expect(Math.min(...gaps)).toBeGreaterThanOrEqual(WEEK_LABEL_PX - 1);
      expect(ticks.map((t) => t.getAttribute('data-week-tick'))).toContain('48');
    }
  });


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
