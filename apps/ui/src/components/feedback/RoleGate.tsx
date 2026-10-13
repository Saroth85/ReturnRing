import type { ReactNode } from 'react';
import type { EndpointKey } from '@/api/client';
import { useRoleGate, type Gate } from './roles';

interface RoleGateProps {
  /** The endpoint the control calls, e.g. `POST /api/returns/{return_id}/decision`. */
  action: EndpointKey;
  /** Render the control; disable it when `allowed` is false. */
  children: (gate: Gate) => ReactNode;
}

/**
 * Disabled with the reason, never hidden (docs/ui-design.md 5.8): the role model reads on
 * screen. The reason comes from the endpoint roles generated from endpoints.json.
 */
export function RoleGate({ action, children }: RoleGateProps) {
  const gate = useRoleGate(action);
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
