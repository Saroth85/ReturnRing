// The contract version check (UI1): the API reports the contract it serves (GET /readyz
// `contract_version`; any other body that carries the field is read the same way), the UI knows
// the one its types were generated from (contract.gen.ts, endpoints.json). A different major or
// minor version means fields may have moved: the shell shows it instead of failing inside a screen.
import { CONTRACT_VERSION } from './contract.gen';

export type ContractStatus = 'same' | 'patch' | 'mismatch' | 'unknown';

export interface ContractCheck {
  status: ContractStatus;
  /** The version the API reports, when it reports one. */
  server: string | null;
  /** The version the UI was generated from. */
  ui: string;
}

const SEMVER = /^(\d+)\.(\d+)\.(\d+)/;

export function compareContract(server: string | null | undefined, ui: string = CONTRACT_VERSION): ContractCheck {
  const s = server ? SEMVER.exec(server) : null;
  const u = SEMVER.exec(ui);
  if (!server || !s || !u) return { status: 'unknown', server: server ?? null, ui };
  if (s[1] !== u[1] || s[2] !== u[2]) return { status: 'mismatch', server, ui };
  return { status: s[3] === u[3] ? 'same' : 'patch', server, ui };
}

/** `contract_version` of an API body, when it carries a string one. */
export function contractVersionOf(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const value = (body as Record<string, unknown>).contract_version;
  return typeof value === 'string' ? value : null;
}
