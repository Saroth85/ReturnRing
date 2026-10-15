// The gates of the writes of the operator and agent areas: the persona's role (endpoints.json) and
// the service's read-only flag (/readyz public_read_only, ADR-15 section 15), with the build flag
// VITE_READ_ONLY only until /readyz answers (useReadOnly). The shared useRoleGate reads the build
// flag alone for now, so these views ask with the service's flag themselves.
import type { EndpointKey } from '@/api/client';
import { usePersona } from '@/app/persona';
import { gateFor, useReadOnly, type Gate } from '@/components/feedback';

export function useWriteGate(action: EndpointKey): Gate {
  const persona = usePersona();
  const readOnly = useReadOnly();
  return gateFor(action, persona.role, readOnly);
}
