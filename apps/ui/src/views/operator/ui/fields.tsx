// Form fields of the operator sheets (docs/ui-design.md 6.3.4): a required reason with a counter,
// a required category, the message language. Native controls: labelled, keyboard-ready.
import clsx from 'clsx';
import { useId } from 'react';
import { MESSAGE_LANGS, OVERRIDE_CATEGORIES } from '@/api/contract.gen';
import type { Lang } from '@/api/models';
import { OPERATOR } from '../copy';
import { MIN_REASON_CHARS, reasonValid } from './validation';

export type OverrideCategory = (typeof OVERRIDE_CATEGORIES)[number];

export function ReasonField({
  value,
  onChange,
  showError,
  label = OPERATOR.sheets.reason,
  hint = OPERATOR.sheets.reasonHint,
  name = 'reason',
}: {
  value: string;
  onChange(value: string): void;
  showError: boolean;
  label?: string;
  hint?: string;
  name?: string;
}) {
  const id = useId();
  const invalid = showError && !reasonValid(value);
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-body font-medium">
        {label} <span aria-hidden="true">*</span>
      </label>
      <textarea
        id={id}
        name={name}
        required
        rows={3}
        value={value}
        aria-invalid={invalid}
        aria-describedby={`${id}-hint`}
        onChange={(e) => onChange(e.target.value)}
        className={clsx(
          'rounded-control border bg-surface px-2 py-1 text-body text-ink-1',
          invalid ? 'border-danger-fg' : 'border-border-strong',
        )}
      />
      <p id={`${id}-hint`} className="flex justify-between gap-2 text-caption text-ink-3">
        <span>{invalid ? <span className="text-danger-fg">{OPERATOR.sheets.reasonTooShort}</span> : hint}</span>
        <span className="tabular-nums" data-constant>
          {value.trim().length}/{MIN_REASON_CHARS}+
        </span>
      </p>
    </div>
  );
}

export function CategorySelect({
  value,
  onChange,
  showError,
  allowed = OVERRIDE_CATEGORIES.filter((c) => c !== 'periodic_review'),
}: {
  value: OverrideCategory | '';
  onChange(value: OverrideCategory | ''): void;
  showError: boolean;
  allowed?: readonly OverrideCategory[];
}) {
  const id = useId();
  const invalid = showError && value === '';
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-body font-medium">
        {OPERATOR.sheets.category} <span aria-hidden="true">*</span>
      </label>
      <select
        id={id}
        name="category"
        required
        value={value}
        aria-invalid={invalid}
        onChange={(e) => onChange(e.target.value as OverrideCategory | '')}
        className={clsx(
          'rounded-control border bg-surface px-2 py-1 text-body text-ink-1',
          invalid ? 'border-danger-fg' : 'border-border-strong',
        )}
      >
        <option value="">{OPERATOR.sheets.chooseCategory}</option>
        {allowed.map((c) => (
          <option key={c} value={c}>
            {OPERATOR.sheets.categories[c] ?? c}
          </option>
        ))}
      </select>
    </div>
  );
}

export function LangChoice({
  value,
  onChange,
  legend = OPERATOR.sheets.messageLanguage,
}: {
  value: Lang;
  onChange(lang: Lang): void;
  legend?: string;
}) {
  const name = useId();
  return (
    <fieldset className="flex flex-wrap items-center gap-3 text-body">
      <legend className="mb-1 font-medium">{legend}</legend>
      {MESSAGE_LANGS.map((lang) => (
        <label key={lang} className="inline-flex items-center gap-1">
          <input type="radio" name={name} value={lang} checked={value === lang} onChange={() => onChange(lang)} />
          <span lang={lang}>{OPERATOR.sheets.langs[lang]}</span>
        </label>
      ))}
    </fieldset>
  );
}
