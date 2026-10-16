// System and benchmarks /system (US-901, US-902, US-905; docs/ui-design.md 9.4; pitch beat 6):
// AMD inference and training, service readiness and telemetry (ADR-18).
import { Cpu, Server } from 'lucide-react';
import { useEffect } from 'react';
import { API_MODE } from '@/api/adapters';
import { useReadyz } from '@/api/queries';
import { usePresent } from '@/app/display';
import { ARCHITECTURE } from '../constants';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';
import { serviceLine } from './readiness';
import { SYSTEM } from './copy';
import { ModeCard, ReadinessPill } from './ModeCard';
import { TelemetryPanel } from './TelemetryPanel';

const s = SYSTEM;

function ArchitectureStrip() {
  const chip = 'rounded-chip border border-border bg-surface px-2 py-1 text-dense';
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface-2 p-4 md:flex-row md:items-center md:gap-6" data-architecture>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-caption font-medium text-ink-2">
          <Cpu aria-hidden="true" size={14} />
          {s.arch.gpu}
        </span>
        {ARCHITECTURE.gpu.map((name) => (
          <span key={name} className={chip} data-constant>
            {name}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-caption font-medium text-ink-2">
          <Server aria-hidden="true" size={14} />
          {s.arch.cpu}
        </span>
        {ARCHITECTURE.cpu.map((name) => (
          <span key={name} className={chip}>
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function SystemPage() {
  const readyz = useReadyz();
  const present = usePresent();
  const service = serviceLine(readyz.data, API_MODE);
  // Beat 6 has no clicks (docs/ui-design.md 12): in presenter mode the page opens on the GPU line,
  // so the first viewport holds it, the cost per decision and the first benchmarks (UI27).
  useEffect(() => {
    if (!present) return;
    const frame = requestAnimationFrame(() => document.getElementById('gpu')?.scrollIntoView({ block: 'start' }));
    return () => cancelAnimationFrame(frame);
  }, [present]);
  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-page="system">
      <PageHeader title={PLATFORM.titles.system} stories={s.stories} aside={<ReadinessPill readyz={readyz.data} />} />
      <nav aria-label={s.onPage} className="flex flex-wrap gap-x-4 gap-y-1 text-body lg:hidden">
        <span className="text-caption text-ink-3">{s.onPage}</span>
        <a href="#telemetry" className="text-accent underline underline-offset-2">
          {s.sections.telemetry}
        </a>
        <a href="#readiness" className="text-accent underline underline-offset-2">
          {s.sections.readiness}
        </a>
      </nav>
      <Panel id="gpu">
        <p className="text-h3" data-constant>
          {s.hero}
        </p>
        <ArchitectureStrip />
        {service && (
          <p className="text-body text-ink-1" data-this-service={readyz.data?.mode ?? 'contract'} data-verbatim>
            {service}
          </p>
        )}
      </Panel>
      <TelemetryPanel />
      <ModeCard />
    </div>
  );
}
