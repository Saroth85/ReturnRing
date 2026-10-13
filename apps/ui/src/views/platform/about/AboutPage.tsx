// About the data /about (US-906, docs/ui-design.md 9.6): the natures of the numbers with an
// example each, what the screens show and what they do not (golden cases, linked accounts,
// forbidden features, who decides), the declared limits and the versions from /readyz. Every
// ProvenancePopover links here.
import type { DisplayNature } from '@/api/models';
import { useReadyz } from '@/api/queries';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { IdText, NatureMark } from '@/components/provenance';
import { NATURE_MEANING, NATURE_WORD } from '@/copy/common';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';

const NATURES: DisplayNature[] = ['real', 'synthetic', 'simulated', 'illustrative'];
const t = PLATFORM.about;

function Versions() {
  const readyz = useReadyz();
  if (readyz.isError) return <ErrorState error={readyz.error} onRetry={() => void readyz.refetch()} />;
  if (!readyz.data) return <LoadingBlock rows={2} />;
  const r = readyz.data;
  const rows: [string, string][] = [
    [t.contract, r.contract_version],
    [t.rules, r.rules_version],
    [t.assumptions, r.assumptions_version],
    ...Object.entries(r.model_versions).flatMap(([name, version]) => (version ? [[name, version] as [string, string]] : [])),
  ];
  return (
    <ul className="flex flex-wrap gap-2 text-caption" data-versions>
      {rows.map(([name, version]) => (
        <li key={name} className="inline-flex items-center gap-1 rounded-chip border border-border px-2 py-0.5 text-ink-2">
          {name} <IdText id={version} className="text-caption" />
        </li>
      ))}
    </ul>
  );
}

export default function AboutPage() {
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="about">
      <PageHeader title={PLATFORM.titles.about} intro={t.intro} stories={t.stories} />
      <Panel title={t.natures} id="natures">
        <p className="text-body text-ink-2">{t.naturesIntro}</p>
        <dl className="grid max-w-4xl gap-x-6 gap-y-3 sm:grid-cols-[auto_1fr]">
          {NATURES.map((n) => (
            <div key={n} className="contents" data-nature-row={n}>
              <dt className="flex items-center gap-2 font-medium">
                <NatureMark nature={n} decorative />
                {NATURE_WORD[n]}
              </dt>
              <dd className="flex flex-col text-ink-2" data-constant>
                <span>{NATURE_MEANING[n]}</span>
                <span className="text-caption text-ink-3">
                  {t.example}: {t.examples[n]}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      </Panel>
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title={t.principles}>
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body">
            <li>{t.decides}</li>
            <li>{t.golden}</li>
            <li>{t.linked}</li>
            <li>{t.forbidden}</li>
            <li>{t.withdrawal}</li>
            <li>{t.refund}</li>
            <li data-constant>{t.agentSteps}</li>
          </ul>
        </Panel>
        <Panel title={t.limits}>
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body">
            {t.limitList.map((line) => (
              <li key={line} data-constant>
                {line}
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <Panel title={t.versions}>
        <p className="text-body text-ink-2">{t.versionsIntro}</p>
        <Versions />
      </Panel>
    </div>
  );
}
