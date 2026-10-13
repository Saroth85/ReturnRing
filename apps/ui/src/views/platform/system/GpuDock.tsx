// GpuDock (shell slot, `T`; docs/ui-design.md 3.4 and 9.4): the "amd-smi in a corner" of spec 16.
// GPU utilisation with its recent line, memory and tokens per second from /api/telemetry, polled
// every interval_s while open and visible. In cached mode it shows the note: no numbers, never zeros.
import { X } from 'lucide-react';
import { useTelemetry } from '@/api/queries';
import type { GpuDockProps } from '@/app/types';
import { Figure } from '@/components/provenance';
import { Kbd } from '@/components/ui';
import { SYSTEM } from './copy';
import { Sparkline } from './Sparkline';

const d = SYSTEM.dock;

export function GpuDock({ open, onClose }: GpuDockProps) {
  const telemetry = useTelemetry(open);
  if (!open) return null;
  const data = telemetry.data;
  const gpu = data?.gpu ?? null;
  const history = data?.gpu_history ?? [];
  return (
    <aside
      aria-label={d.title}
      className="fixed right-4 bottom-10 z-40 flex w-72 flex-col gap-1 rounded-card border border-border bg-surface p-3 shadow-overlay"
      data-gpu-dock
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-micro uppercase text-ink-3">
          {d.title}
          {data?.device && (
            <span className="normal-case tracking-normal" data-constant>
              {' '}
              · {data.device}
            </span>
          )}
        </span>
        <button type="button" onClick={onClose} aria-label={d.close} className="rounded-control p-1 hover:bg-surface-2">
          <X aria-hidden="true" size={14} />
        </button>
      </div>
      {gpu ? (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-h3">
              <Figure metric={gpu.utilization} label={SYSTEM.gpuUtil} />
            </span>
            <Sparkline values={history.map((h) => h.utilization.value)} min={0} max={100} className="h-8 w-28" />
          </div>
          <p className="flex flex-wrap items-baseline gap-x-1 text-caption text-ink-2">
            {d.memory} <Figure metric={gpu.memory_used} label={SYSTEM.memoryUsed} /> {SYSTEM.memoryOf}{' '}
            <Figure metric={gpu.memory_total} label={SYSTEM.memory} />
            {data?.llm && (
              <>
                {' · '}
                <Figure metric={data.llm.tokens_per_second} label={SYSTEM.tokens} />
              </>
            )}
          </p>
        </>
      ) : (
        data?.note && (
          <p className="text-caption text-ink-2" data-verbatim>
            {data.note}
          </p>
        )
      )}
      <p className="text-caption text-ink-3">
        <Kbd>T</Kbd> {d.hint}
      </p>
    </aside>
  );
}
