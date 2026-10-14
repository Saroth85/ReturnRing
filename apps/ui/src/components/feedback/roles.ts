import type { EndpointKey } from '@/api/client';
import { ENDPOINTS } from '@/api/contract.gen';
import type { Role } from '@/api/models';
import { usePersona } from '@/app/persona';
import { FEEDBACK, SHELL } from '@/copy/common';

export interface Gate {
  allowed: boolean;
  /** Why the control is disabled, printed under it; null when allowed. */
  reason: string | null;
}

/** VITE_READ_ONLY=true builds the public read-only demo (docs/ui-design.md 3.1, O-14). */
export const READ_ONLY = import.meta.env.VITE_READ_ONLY === 'true';

/**
 * POST routes that change nothing, so the read-only public demo keeps them (O-14): the agent only
 * reads the case (spec 8.8), and in the cached demo it answers only the precomputed questions.
 */
export const READ_ONLY_SAFE_POSTS: ReadonlySet<EndpointKey> = new Set<EndpointKey>(['POST /api/agent/ask']);

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

export function useRoleGate(action: EndpointKey): Gate {
  const persona = usePersona();
  return gateFor(action, persona.role);
}
