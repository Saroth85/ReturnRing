import clsx from 'clsx';
import { RotateCcw } from 'lucide-react';
import { useId } from 'react';
import type { InputKey } from '@/sim';
import { MANAGER } from '../copy';
import type { AssumptionVersion } from '../types';
import { COUNT_KEYS, fieldError, type Draft } from './model';

const S = MANAGER.simulator;

const GROUPS: { title: string; keys: InputKey[] }[] = [
  { title: S.form.groups.volumes, keys: ['logistics_returns_avoided', 'undue_discounts_avoided', 'minutes_saved'] },
  { title: S.form.groups.unit, keys: ['logistics_cost_per_return_eur', 'avg_undue_discount_eur', 'hourly_cost_eur'] },
  { title: S.form.groups.costs, keys: ['incentives_eur', 'system_cost_eur', 'error_cost_eur', 'abandonment_margin_lost_eur'] },
  { title: S.form.groups.margin, keys: ['incremental_margin_eur'] },
];

/** The slider range: twice the stored value, at least a small span so a zero can move. */
const MIN_SLIDER_MAX = 10;
const sliderMax = (stored: number) => Math.max(MIN_SLIDER_MAX, Math.ceil(stored * 2));

function Field({
  name,
  value,
  stored,
  onChange,
}: {
  name: InputKey;
  value: string;
  stored: number;
  onChange(value: string): void;
}) {
  const id = useId();
  const errorId = useId();
  const meta = S.fields[name] ?? { label: name, unit: '' };
  const error = fieldError(name, value);
  const numeric = Number(value.replace(',', '.'));
  const changed = !error && numeric !== stored;
  const step = COUNT_KEYS.has(name) ? 1 : name === 'minutes_saved' ? 1 : 0.01;
  return (
    <div className="flex flex-col gap-1" data-field={name} data-changed={changed || undefined}>
      <label htmlFor={id} className="flex items-baseline justify-between gap-2 text-dense">
        <span className={clsx(changed ? 'font-medium text-ink-1' : 'text-ink-2')}>{meta.label}</span>
        <span className="text-caption text-ink-3">{meta.unit}</span>
      </label>
      <div className="flex items-center gap-2">
        <input
          type="range"
          aria-hidden="true"
          tabIndex={-1}
          min={0}
          max={sliderMax(stored)}
          step={step}
          value={error ? stored : numeric}
          onChange={(event) => onChange(event.target.value)}
          className="min-w-0 flex-1 accent-[var(--accent)]"
        />
        <input
          id={id}
          inputMode="decimal"
          value={value}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => onChange(event.target.value)}
          className={clsx(
            'w-24 rounded-control border bg-surface px-2 py-1 text-right text-body tabular-nums',
            error ? 'border-danger-fg' : changed ? 'border-ink-1' : 'border-border-strong',
          )}
        />
      </div>
      {error && (
        <p id={errorId} className="text-caption text-danger-fg">
          {error}
        </p>
      )}
    </div>
  );
}

interface AssumptionFormProps {
  draft: Draft;
  version: AssumptionVersion;
  dirty: boolean;
  onChange(key: InputKey, value: string): void;
  onReset(): void;
}

/**
 * Every input of the simulator with its unit, grouped (docs/ui-design.md 8.2): a slider plus a
 * number field. There is no refund field (rule 9). Edits are an unsaved scenario computed here.
 */
export function AssumptionForm({ draft, version, dirty, onChange, onReset }: AssumptionFormProps) {
  const stored = version.net_benefit.assumptions;
  return (
    <form className="flex flex-col gap-4" onSubmit={(event) => event.preventDefault()} data-assumption-form>
      {GROUPS.map((group) => (
        <fieldset key={group.title} className="flex flex-col gap-3">
          <legend className="mb-1 text-micro uppercase text-ink-3">{group.title}</legend>
          {group.keys.map((key) => (
            <Field key={key} name={key} value={draft[key]} stored={stored[key]} onChange={(value) => onChange(key, value)} />
          ))}
        </fieldset>
      ))}
      <p className="text-caption text-ink-2">{S.form.noRefund}</p>
      {dirty && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-control border border-dashed border-ink-2 px-2 py-1.5" data-unsaved>
          <span className="text-caption font-medium">{S.form.unsaved}</span>
          <button type="button" onClick={onReset} className="inline-flex items-center gap-1 text-caption text-accent underline underline-offset-2">
            <RotateCcw aria-hidden="true" size={12} />
            {S.form.reset}
          </button>
        </div>
      )}
    </form>
  );
}
