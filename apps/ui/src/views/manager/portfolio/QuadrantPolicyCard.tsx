import { useReadyz } from '@/api/queries';
import { QuadrantGlyph } from '@/components/decision';
import { Q1_WINDOW_DAYS, Q4_WINDOW_DAYS, STANDARD_WINDOW_DAYS } from '../constants';
import { MANAGER } from '../copy';

const P = MANAGER.portfolio.policy;

const ROWS = [
  { q: 'Q1', days: Q1_WINDOW_DAYS, legal: false, terms: P.Q1 },
  { q: 'Q2', days: STANDARD_WINDOW_DAYS, legal: false, terms: P.Q2 },
  { q: 'Q3', days: STANDARD_WINDOW_DAYS, legal: false, terms: P.Q3 },
  { q: 'Q4', days: Q4_WINDOW_DAYS, legal: true, terms: P.Q4 },
] as const;

/**
 * The lever table of spec 8.6 per quadrant (docs/ui-design.md 8.1 QuadrantPolicyCard), labelled as
 * a static summary of the rules version until the API exposes the table (open question O-19).
 */
export function QuadrantPolicyCard() {
  const readyz = useReadyz();
  const rules = readyz.data?.rules_version;
  return (
    <section className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4" data-policy-card>
      <header className="flex flex-col gap-1">
        <h2 className="text-h3">{P.title}</h2>
        <p className="text-caption text-ink-3">
          {P.staticNote}{' '}
          {rules && (
            <span className="font-mono" data-version>
              {rules}
            </span>
          )}{' '}
          {P.staticTail}
        </p>
      </header>
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {ROWS.map((row) => (
          <li key={row.q} className="flex flex-col gap-1" data-policy={row.q}>
            <p className="flex items-center gap-1.5 font-medium">
              <QuadrantGlyph quadrant={row.q} />
              <span data-id>{row.q}</span>
              <span className="text-caption font-normal text-ink-3">{MANAGER.portfolio.quadrantTitle[row.q]}</span>
            </p>
            <ul className="list-disc pl-5 text-dense text-ink-2">
              <li>
                <span data-constant>{row.days}</span>-{P.days} {row.legal && P.legalMinimum}
              </li>
              {row.terms.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <p className="text-caption text-ink-2">{P.futureOnly}</p>
    </section>
  );
}
