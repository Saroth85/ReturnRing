// The acting user (docs/ui-design.md 3.1). No authentication (spec 4): the persona chosen in the
// top bar fills every actor field (approver, author, requested_by, asked_by, inspector). Stored in
// localStorage when available; Sara by default.
import { DEMO_USERS } from '@/api/contract.gen';
import type { DemoUserId, Role } from '@/api/models';
import { createStore, safeStorage, useStore } from '@/api/store';

export type Persona = (typeof DEMO_USERS)[number];

const KEY = 'returnring.persona';
const DEFAULT_ID: DemoUserId = 'sara';

function byId(id: string | null): Persona | undefined {
  return DEMO_USERS.find((u) => u.id === id);
}

function initialPersona(): Persona {
  const stored = typeof window === 'undefined' ? null : safeStorage.get('local', KEY);
  const persona = byId(stored) ?? byId(DEFAULT_ID) ?? DEMO_USERS[0];
  return persona;
}

export const personaStore = createStore<Persona>(initialPersona());

export function setPersona(id: DemoUserId): void {
  const persona = byId(id);
  if (!persona) throw new Error(`unknown demo user ${id}`);
  safeStorage.set('local', KEY, persona.id);
  personaStore.set(persona);
}

export const usePersona = () => useStore(personaStore);

/** Where `/` leads each role (docs/ui-design.md 3.1). */
export const LANDING: Record<Role, string> = {
  operator: '/queue',
  manager: '/portfolio',
  warehouse: '/inspection',
};

export function personaName(id: string): string {
  return id.charAt(0).toUpperCase() + id.slice(1);
}
