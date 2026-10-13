// Lever vocabulary of the UI (docs/ui-design.md 5.3): mirrors contracts/levers.py (ids, value
// types, scopes, the legal minimum of the return window) and adds what the UI needs to show a
// lever: its direction against the base terms. Pure functions; the labels live with the
// decision components (src/components/decision/levers.ts).
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import type { Lever, Terms } from '@/api/models';

export type LeverId = Lever['id'];
export type LeverScope = Lever['scope'];
/** A change against the base terms: less generous, more generous, or none. */
export type Direction = 'less' | 'more' | 'same';
export type TermKey = keyof Terms;

interface LeverSpec {
  id: LeverId;
  valueType: 'integer' | 'boolean';
  scope: LeverScope;
  /** The Terms field the lever sets, for the five term levers. */
  termKey: TermKey | null;
  minimum: number | null;
  /** For the levers outside Terms: the direction of `true` (false is no change). */
  whenTrue: Direction | null;
}

/** contracts/levers.py LEVERS, in the same order. */
export const LEVER_SPECS: readonly LeverSpec[] = [
  {
    id: 'return_window_days',
    valueType: 'integer',
    scope: 'future_orders',
    termKey: 'return_window_days',
    minimum: LEGAL_MINIMUM_WINDOW_DAYS,
    whenTrue: null,
  },
  {
    id: 'free_return_shipping',
    valueType: 'boolean',
    scope: 'future_orders',
    termKey: 'free_return_shipping',
    minimum: null,
    whenTrue: null,
  },
  { id: 'instant_refund', valueType: 'boolean', scope: 'future_orders', termKey: 'instant_refund', minimum: null, whenTrue: null },
  { id: 'free_size_exchange', valueType: 'boolean', scope: 'all_orders', termKey: 'free_size_exchange', minimum: null, whenTrue: null },
  { id: 'coupons_enabled', valueType: 'boolean', scope: 'future_orders', termKey: 'coupons_enabled', minimum: null, whenTrue: null },
  { id: 'cluster_coupons_disabled', valueType: 'boolean', scope: 'future_orders_cluster', termKey: null, minimum: null, whenTrue: 'less' },
  { id: 'transparency_notice', valueType: 'boolean', scope: 'communication', termKey: null, minimum: null, whenTrue: 'less' },
  { id: 'size_advice', valueType: 'boolean', scope: 'current_order', termKey: null, minimum: null, whenTrue: 'more' },
  { id: 'priority_size_exchange', valueType: 'boolean', scope: 'all_orders', termKey: null, minimum: null, whenTrue: 'more' },
];

const SPEC_BY_ID = new Map(LEVER_SPECS.map((s) => [s.id, s]));

export function leverSpec(id: LeverId): LeverSpec {
  const spec = SPEC_BY_ID.get(id);
  if (!spec) throw new Error(`unknown lever ${id}`);
  return spec;
}

/** The "id:value" form of contracts/levers.py format_lever_id, e.g. `instant_refund:false`. */
export function leverKey(lever: Pick<Lever, 'id' | 'value'>): string {
  return `${lever.id}:${typeof lever.value === 'boolean' ? String(lever.value) : String(lever.value)}`;
}

/** Inverse of leverKey, with the scope from the catalogue (contracts/levers.py lever_from_id). */
export function parseLeverKey(text: string): Lever {
  const [id, raw] = text.split(':') as [string, string | undefined];
  const spec = SPEC_BY_ID.get(id as LeverId);
  if (!spec || raw === undefined) throw new Error(`not a lever id: ${text}`);
  if (spec.valueType === 'boolean') {
    if (raw !== 'true' && raw !== 'false') throw new Error(`${id} takes true or false, got ${raw}`);
    return { id: spec.id, value: raw === 'true', scope: spec.scope };
  }
  if (!/^(0|[1-9][0-9]*)$/.test(raw)) throw new Error(`${id} takes an integer, got ${raw}`);
  return { id: spec.id, value: Number(raw), scope: spec.scope };
}

/** Whether a lever gives less, more, or the same as the base terms (docs/ui-design.md 5.3). */
export function leverDirection(lever: Pick<Lever, 'id' | 'value'>, base: Terms): Direction {
  const spec = leverSpec(lever.id);
  if (spec.termKey) {
    const before = base[spec.termKey];
    if (typeof before === 'number' && typeof lever.value === 'number') {
      return lever.value < before ? 'less' : lever.value > before ? 'more' : 'same';
    }
    if (lever.value === before) return 'same';
    return lever.value === false ? 'less' : 'more';
  }
  return lever.value === true ? (spec.whenTrue ?? 'same') : 'same';
}

export const isRestrictiveLever = (lever: Pick<Lever, 'id' | 'value'>, base: Terms): boolean => leverDirection(lever, base) === 'less';

export const hasRestrictiveLever = (levers: readonly Pick<Lever, 'id' | 'value'>[], base: Terms): boolean =>
  levers.some((l) => isRestrictiveLever(l, base));

/** The value a lever has in the base terms, or its neutral value outside Terms. */
export function baseValue(id: LeverId, base: Terms): number | boolean {
  const spec = leverSpec(id);
  return spec.termKey ? base[spec.termKey] : false;
}

/** Lever values keyed by id: what the override editor holds (docs/ui-design.md 6.3.4). */
export type LeverValues = { [K in LeverId]: number | boolean };

/** Base terms with the given levers applied on top. */
export function leverValues(levers: readonly Pick<Lever, 'id' | 'value'>[], base: Terms): LeverValues {
  const values = Object.fromEntries(LEVER_SPECS.map((s) => [s.id, baseValue(s.id, base)])) as LeverValues;
  for (const lever of levers) values[lever.id] = lever.value;
  return values;
}

/** The levers of a set of values: every value that differs from the base, in catalogue order. */
export function leversFromValues(values: LeverValues, base: Terms): Lever[] {
  return LEVER_SPECS.filter((s) => values[s.id] !== baseValue(s.id, base)).map((s) => ({
    id: s.id,
    value: values[s.id],
    scope: s.scope,
  })) as Lever[];
}

/** Two lever lists name the same levers and values, in any order. */
export function sameLevers(a: readonly Pick<Lever, 'id' | 'value'>[], b: readonly Pick<Lever, 'id' | 'value'>[]): boolean {
  const ka = a.map(leverKey).sort();
  const kb = b.map(leverKey).sort();
  return ka.length === kb.length && ka.every((k, i) => k === kb[i]);
}
