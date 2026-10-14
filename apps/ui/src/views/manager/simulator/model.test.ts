// The simulator screen's logic (US-602, ADR-04, docs/ui-design.md 8.2): server values while the
// assumptions are the stored ones (checked against the browser port), browser values with their own
// provenance once edited, versioned labels, and the PUT body.
import { describe, expect, it } from 'vitest';
import type { SimulatorAssumptionsResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { MANAGER } from '../copy';
import { draftOf, fieldError, inputsFromDraft, isDirty, nextVersionLabel, scenarioViews, updateBody } from './model';

const v1 = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v1.json').current;
const v2 = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v2.json').current;

describe('draft', () => {
  it('starts from the stored version and is clean', () => {
    const draft = draftOf(v1);
    expect(draft.logistics_cost_per_return_eur).toBe('8');
    expect(isDirty(draft, v1)).toBe(false);
    expect(inputsFromDraft(draft, v1)).toMatchObject({ logistics_returns_avoided: 80, minutes_saved: 240 });
  });

  it('is dirty after an edit, and an invalid field keeps the stored value', () => {
    const draft = { ...draftOf(v1), logistics_cost_per_return_eur: '4' };
    expect(isDirty(draft, v1)).toBe(true);
    expect(inputsFromDraft({ ...draft, incentives_eur: '-5' }, v1).incentives_eur).toBe(200);
  });

  it('accepts decimal commas and refuses negatives and fractions of a count', () => {
    expect(fieldError('logistics_cost_per_return_eur', '6,50')).toBeNull();
    expect(inputsFromDraft({ ...draftOf(v1), logistics_cost_per_return_eur: '6,50' }, v1).logistics_cost_per_return_eur).toBe(6.5);
    expect(fieldError('incentives_eur', '-1')).not.toBeNull();
    expect(fieldError('incentives_eur', '')).not.toBeNull();
    expect(fieldError('logistics_returns_avoided', '2.5')).not.toBeNull();
    expect(fieldError('minutes_saved', '2.5')).toBeNull();
  });

  it('refuses euro amounts with more than two decimals, as the API does (UI4)', () => {
    expect(fieldError('logistics_cost_per_return_eur', '8.125')).toBe(MANAGER.simulator.form.eurDecimals);
    expect(fieldError('logistics_cost_per_return_eur', '8,12')).toBeNull();
    expect(fieldError('incentives_eur', '200.5')).toBeNull();
    expect(inputsFromDraft({ ...draftOf(v1), logistics_cost_per_return_eur: '8.125' }, v1).logistics_cost_per_return_eur).toBe(
      v1.net_benefit.assumptions.logistics_cost_per_return_eur,
    );
  });
});

describe('scenarioViews', () => {
  it('shows the server values of the stored version when the browser agrees', () => {
    const views = scenarioViews(draftOf(v1), v1);
    expect(views.map((v) => [v.id, v.source, v.view.total.value])).toEqual([
      ['base', 'server', 368],
      ['logistics_halved', 'server', 48],
      ['abandonment_doubled', 'server', 128],
      ['both', 'server', -192],
    ]);
    expect(views.every((v) => !v.differs)).toBe(true);
    expect(views[3]?.view.assumptions.version).toBe('assumptions-v1/both');
  });

  it('computes an edited draft in the browser, simulated, with its own origin and the server period', () => {
    const views = scenarioViews({ ...draftOf(v1), logistics_cost_per_return_eur: '6.5' }, v1);
    expect(views.map((v) => v.view.total.value)).toEqual([248, -12, 8, -252]);
    const base = views[0]!.view;
    expect(views[0]?.source).toBe('browser');
    expect(base.total.provenance.nature).toBe('simulated');
    expect(base.total.provenance.origin).toMatch(/Computed in your browser/);
    expect(base.total.provenance.period_start).toBe(v1.net_benefit.total.provenance.period_start);
    expect(base.assumptions.based_on).toBe('assumptions-v1');
  });

  it('shows the server value, flagged, when the browser disagrees', () => {
    const forged = structuredClone(v1);
    forged.sensitivity[1]!.net_benefit.total.value = 50;
    const views = scenarioViews(draftOf(forged), forged);
    expect(views[1]).toMatchObject({ source: 'server', differs: true });
    expect(views[1]?.view.total.value).toBe(50);
  });

  it('reproduces v2 after the save', () => {
    expect(scenarioViews(draftOf(v2), v2).map((v) => v.view.total.value)).toEqual([248, -12, 8, -252]);
  });
});

describe('save', () => {
  it('builds the PUT body of the contract example from the edited draft', () => {
    const example = readExample<ReturnType<typeof updateBody>>('simulator_assumptions_update.json');
    const draft = { ...draftOf(v1), logistics_cost_per_return_eur: '6.50' };
    expect(updateBody(draft, v1, example.note, 'luca')).toEqual(example);
  });

  it('names the next version', () => {
    expect(nextVersionLabel('assumptions-v1')).toBe('assumptions-v2');
    expect(nextVersionLabel('assumptions-v9')).toBe('assumptions-v10');
    expect(nextVersionLabel('custom')).toBe('a new version');
  });
});
