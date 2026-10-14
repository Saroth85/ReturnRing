import type { Unit } from '@/api/models';
import { formatValue } from '@/format';
import { useProvenanceScope } from './scope';

interface PlainNumberProps {
  value: number;
  unit: Unit;
  signed?: boolean;
  /** Fraction digits a float count may show (minutes saved: 7.5). */
  maxFractionDigits?: number;
}

/**
 * A number that is an attribute of a record (a price, a link weight, a SHAP value, a declared
 * confidence): its section's provenance covers it (ADR-15 section 2). Throws in dev builds
 * outside a ProvenanceScope, so no number reaches the screen without provenance (rule 11).
 */
export function PlainNumber({ value, unit, signed = false, maxFractionDigits }: PlainNumberProps) {
  const scope = useProvenanceScope();
  if (!scope && import.meta.env.DEV) {
    throw new Error(`PlainNumber ${value} (${unit}) outside a ProvenanceScope: every number needs a provenance (rule 11, US-906)`);
  }
  return (
    <span data-plain-number className="tabular-nums">
      {formatValue(value, unit, { signed, maxFractionDigits })}
    </span>
  );
}

/** A euro amount under the enclosing ProvenanceScope. */
export function Money({ value, signed = false }: { value: number; signed?: boolean }) {
  return <PlainNumber value={value} unit="eur" signed={signed} />;
}
