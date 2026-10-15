// The write gate of the manager screens: the role of the acting persona (endpoints.json) and the
// public read-only demo as /readyz states it (public_read_only, ADR-15 section 15). A local stand-in
// until the shared useRoleGate reads useReadOnly() itself (it still reads the build flag only):
// then RoleGate replaces WriteGate with no change on screen.
import type { EndpointKey } from '@/api/client';
import { usePersona } from '@/app/persona';
import { gateFor, useReadOnly, type Gate } from '@/components/feedback';

export function useWriteGate(action: EndpointKey): Gate {
  const persona = usePersona();
  const readOnly = useReadOnly();
  return gateFor(action, persona.role, readOnly);
}
