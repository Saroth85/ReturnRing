import type { EndpointKey } from '@/api/client';
import { ENDPOINTS } from '@/api/contract.gen';
import type { Role } from '@/api/models';
import { useReadyz } from '@/api/queries';
import { usePersona } from '@/app/persona';
import { FEEDBACK, SHELL } from '@/copy/common';

export interface Gate {
  allowed: boolean;
  /** Why the control is disabled, printed under it; null when allowed. */
  reason: string | null;
}

/**
 * VITE_READ_ONLY=true builds the public read-only demo (docs/ui-design.md 3.1, O-14): the
 * fallback until /readyz answers. The service decides: use useReadOnly().
 */
export const READ_ONLY = import.meta.env.VITE_READ_ONLY === 'true';

/**
 * Whether the views are read-only: /readyz `public_read_only` (PUBLIC_READ_ONLY of the API, ADR-15
 * section 15), which refuses every write but the two below with 403; the build flag
 * VITE_READ_ONLY while /readyz has not answered.
 */
export function useReadOnly(): boolean {
  const readyz = useReadyz();
  return readyz.data?.public_read_only ?? READ_ONLY;
}

/**
 * POST routes the read-only public demo keeps (ADR-15 section 15, O-14): the agent only reads the
 * case (spec 8.8), and the checkout evaluation shows terms without storing a snapshot. Both are
 * rate limited per client (429 with Retry-After).
 */
export const READ_ONLY_SAFE_POSTS: ReadonlySet<EndpointKey> = new Set<EndpointKey>([
  'POST /api/agent/ask',
  'POST /api/checkout/evaluate',
]);

/** Whether a role may call an endpoint, from the roles of endpoints.json (ADR-15 section 5). */
export function gateFor(action: EndpointKey, role: Role, readOnly = READ_ONLY): Gate {
  const [method, path] = action.split(' ');
  const endpoint = ENDPOINTS.find((e) => e.method === method && e.path === path);
  if (!endpoint) throw new Error(`RoleGate: ${action} is not in endpoints.json`);
  if (readOnly && method !== 'GET' && !READ_ONLY_SAFE_POSTS.has(action)) return { allowed: false, reason: FEEDBACK.readOnlyDemo };
  const roles = endpoint.roles as readonly string[];
  if (roles.includes(role)) return { allowed: true, reason: null };
  const allowed = roles.map((r) => `${SHELL.roles[r] ?? r}s`).join(' and ');
  return { allowed: false, reason: FEEDBACK.roleReason(allowed, SHELL.roles[role] ?? role) };
}

/**
 * The gate of a write for the acting persona: the endpoint's roles, and the read-only public demo
 * as /readyz states it (useReadOnly). RoleGate renders it; a view that lays the reason out itself
 * reads it here.
 */
export function useRoleGate(action: EndpointKey): Gate {
  const persona = usePersona();
  const readOnly = useReadOnly();
  return gateFor(action, persona.role, readOnly);
}

/**
 * Whether a 403 on `action` is the read-only public demo refusing a write, not the persona's role:
 * the service is read-only and the persona's role may call the endpoint. Without an endpoint, any
 * 403 while read-only counts as the read-only refusal.
 */
export function isReadOnlyRefusal(readOnly: boolean, role: Role, action?: EndpointKey): boolean {
  if (!readOnly) return false;
  if (!action) return true;
  return gateFor(action, role, true).reason === FEEDBACK.readOnlyDemo;
}
