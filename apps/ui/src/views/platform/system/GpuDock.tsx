// GpuDock (shell slot, `T`; docs/ui-design.md 3.4): the "amd-smi in a corner" of spec 16. Stub:
// GPU utilisation from /api/telemetry, or the cached-mode note (no numbers, never zeros).
import { X } from 'lucide-react';
import { useTelemetry } from '@/api/queries';
import type { GpuDockProps } from '@/app/types';
import { Figure } from '@/components/provenance';
import { PLATFORM } from '../copy';

export function GpuDock({ open, onClose }: GpuDockProps) {
  const telemetry = useTelemetry(open);
  if (!open) return null;
  const data = telemetry.data;
  return (
    <aside
      aria-label={PLATFORM.gpu.title}
      className="fixed right-4 bottom-10 z-40 flex w-72 flex-col gap-1 rounded-card border border-border bg-surface p-3 shadow-overlay"
      data-gpu-dock
    >
      <div className="flex items-center justify-between">
        <span className="text-micro uppercase text-ink-3">{PLATFORM.gpu.title}</span>
        <button type="button" onClick={onClose} aria-label={PLATFORM.gpu.close} className="rounded-control p-1 hover:bg-surface-2">
          <X aria-hidden="true" size={14} />
        </button>
      </div>
      {data?.gpu ? (
        <p>
          <Figure metric={data.gpu.utilization} label={PLATFORM.gpu.utilisation} />
        </p>
      ) : (
        data?.note && <p className="text-caption text-ink-2">{data.note}</p>
      )}
      <p className="text-caption text-ink-3">{PLATFORM.gpu.hint}</p>
    </aside>
  );
}
