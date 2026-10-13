import clsx from 'clsx';
import { Table2, LineChart } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import type { Provenance } from '@/api/models';
import { ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { CHARTS } from './copy';

export interface ChartTable {
  caption: string;
  columns: string[];
  /** Cells render numbers with PlainNumber (covered by the frame's provenance) or Figure. */
  rows: { key: string; cells: ReactNode[] }[];
}

interface ChartFrameProps {
  title: string;
  /** A line under the title: what is plotted, the period when it differs from the scope. */
  subtitle?: ReactNode;
  /** The provenance of the plotted numbers: it covers axis ticks, tooltips and table cells. */
  provenance: Provenance;
  covers: string;
  table: ChartTable;
  children: ReactNode;
  /** Controls next to the table toggle (a legend, a filter). */
  actions?: ReactNode;
  /** Start on the table (the accessible primary view). */
  defaultView?: 'chart' | 'table';
  className?: string;
  /** Heading level of the title (default 3). */
  level?: 2 | 3;
}

/**
 * A chart with its title, a "View as table" toggle and the provenance footer (docs/ui-design.md
 * 4.8, 5.6). The frame is a ProvenanceScope, so axis ticks and table numbers carry the origin,
 * period and nature of the data (rule 11, US-906). Every chart has a table view (WCAG, 10.3).
 */
export function ChartFrame({
  title,
  subtitle,
  provenance,
  covers,
  table,
  children,
  actions,
  defaultView = 'chart',
  className,
  level = 3,
}: ChartFrameProps) {
  const [view, setView] = useState<'chart' | 'table'>(defaultView);
  const titleId = useId();
  const Heading = level === 2 ? 'h2' : 'h3';
  const asTable = view === 'table';
  return (
    <ProvenanceScope provenance={provenance} covers={covers} className={clsx('flex min-w-0 flex-col gap-2', className)}>
      <section aria-labelledby={titleId} className="flex min-w-0 flex-col gap-2" data-chart-frame>
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <Heading id={titleId} className={level === 2 ? 'text-h2' : 'text-h3'}>
              {title}
            </Heading>
            {subtitle && <div className="text-caption text-ink-2">{subtitle}</div>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {actions}
            <button
              type="button"
              aria-pressed={asTable}
              onClick={() => setView(asTable ? 'chart' : 'table')}
              className="inline-flex min-h-8 items-center gap-1 rounded-control border border-border-strong bg-surface px-2 text-caption text-ink-1 hover:bg-surface-2"
            >
              {asTable ? <LineChart aria-hidden="true" size={14} /> : <Table2 aria-hidden="true" size={14} />}
              {asTable ? CHARTS.viewChart : CHARTS.viewTable}
            </button>
          </div>
        </header>
        {asTable ? (
          <div className="relative overflow-x-auto">
            <table className="w-full border-collapse text-dense tabular-nums" data-chart-table>
              <caption className="sr-only">{table.caption}</caption>
              <thead>
                <tr className="border-b border-border-strong text-left text-caption text-ink-2">
                  {table.columns.map((c) => (
                    <th key={c} scope="col" className="px-2 py-1 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row) => (
                  <tr key={row.key} className="border-b border-border">
                    {row.cells.map((cell, i) => (
                      <td key={i} className="px-2 py-1">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="min-w-0">{children}</div>
        )}
      </section>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}
