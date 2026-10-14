import type { TemplateFieldName } from '@/api/models';
import { MESSAGE } from '@/copy/common';

interface TemplateFieldProps {
  name: TemplateFieldName;
  value: string | null;
  /** "Preview as the customer": the text as sent, without chip styling or label. */
  asCustomer?: boolean;
}

/**
 * A template field filled by code (rule 10): never written by the language model, selectable,
 * never editable. A null value is a code bug and shows in danger (docs/ui-design.md 5.4).
 */
export function TemplateField({ name, value, asCustomer = false }: TemplateFieldProps) {
  const tooltip = MESSAGE.fieldTooltip(name, MESSAGE.fieldSource[name]);
  if (value === null) {
    return (
      <span data-template-field={name} data-missing className="font-medium text-danger-fg">
        {MESSAGE.missingField(name)}
      </span>
    );
  }
  if (asCustomer) {
    return <span data-template-field={name}>{value}</span>;
  }
  return (
    <span
      data-template-field={name}
      title={tooltip}
      className="rounded-chip border border-field-border bg-field-bg px-1 text-field-fg [box-decoration-break:clone]"
    >
      <span className="mr-1 align-[1px] text-micro uppercase" aria-hidden="true">
        {MESSAGE.fieldLabels[name]}
      </span>
      <span className="sr-only">{`${MESSAGE.fieldLabels[name]}: `}</span>
      {value}
    </span>
  );
}
