import type { ReactNode } from 'react';
import type { EndpointKey } from '@/api/client';
import type { Gate } from '@/components/feedback';
import { useWriteGate } from './gate';

/** RoleGate with the read-only demo of /readyz: the control, and its reason under it. */
export function WriteGate({ action, children }: { action: EndpointKey; children: (gate: Gate) => ReactNode }) {
  const gate = useWriteGate(action);
  return (
    <div data-role-gate={gate.allowed ? 'allowed' : 'blocked'} className="inline-flex flex-col gap-1">
      {children(gate)}
      {gate.reason && (
        <p className="text-caption text-ink-3" data-role-reason>
          {gate.reason}
        </p>
      )}
    </div>
  );
}
