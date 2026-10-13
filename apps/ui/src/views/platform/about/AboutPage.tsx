// About the data /about (US-906, docs/ui-design.md 9.6): the natures of the numbers, golden
// cases, limits, linked accounts, forbidden features, versions. Every ProvenancePopover links here.
import type { DisplayNature } from '@/api/models';
import { useReadyz } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { NatureMark } from '@/components/provenance';
import { NATURE_MEANING, NATURE_WORD } from '@/copy/common';
import { PLATFORM } from '../copy';

const NATURES: DisplayNature[] = ['real', 'synthetic', 'simulated', 'illustrative'];

export default function AboutPage() {
  const readyz = useReadyz();
  const t = PLATFORM.about;
  return (
    <ScreenPlaceholder title={PLATFORM.titles.about} area="platform" stories={['US-906']} spec="docs/ui-design.md 9.6">
      <section id="natures" className="flex flex-col gap-2">
        <h2 className="text-h3">{t.natures}</h2>
        <dl className="grid max-w-3xl grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          {NATURES.map((n) => (
            <div key={n} className="contents">
              <dt className="flex items-center gap-2 font-medium">
                <NatureMark nature={n} decorative />
                {NATURE_WORD[n]}
              </dt>
              <dd className="text-ink-2" data-constant>
                {NATURE_MEANING[n]}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      <ul className="flex max-w-3xl list-disc flex-col gap-1 pl-6 text-body">
        <li>{t.golden}</li>
        <li>{t.linked}</li>
        <li>{t.forbidden}</li>
        <li>{t.limits}</li>
        <li data-constant>{t.agentSteps}</li>
      </ul>
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.versions}</h2>
        <DataCheck label="GET /readyz" result={readyz} />
        {readyz.data && (
          <ul className="flex flex-wrap gap-2 text-caption">
            {Object.entries(readyz.data.model_versions).map(([name, version]) => (
              <li key={name} className="rounded-chip border border-border px-2 py-0.5">
                {name} <span data-id>{version}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </ScreenPlaceholder>
  );
}
