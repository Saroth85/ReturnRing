// Before the live suite: an optional reset (UI_E2E_RESET_CMD), then the API must answer /readyz
// with the contract version this UI was generated from (same major.minor, docs/ui-design.md 11.4)
// and hold the demo cases of a reset (GET /api/demo/cases 200). A failure here names the fix,
// instead of thirty specs failing on an empty or older store.
import { execSync } from 'node:child_process';
import { CONTRACT_VERSION } from '../../../src/api/contract.gen';
import { API_URL } from './env';

const SEMVER = /^(\d+)\.(\d+)\./;

async function getJson(path: string): Promise<{ status: number; body: unknown }> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { headers: { accept: 'application/json' } });
  } catch (error) {
    throw new Error(`live e2e: ${API_URL}${path} did not answer (${String(error)}). Start the API and set RETURNRING_API_URL.`);
  }
  const text = await response.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    // Not JSON: kept as text for the message below.
  }
  return { status: response.status, body };
}

export default async function globalSetup(): Promise<void> {
  const reset = process.env.UI_E2E_RESET_CMD;
  if (reset) {
    console.log(`live e2e: ${reset}`);
    execSync(reset, { stdio: 'inherit' });
  }

  const readyz = await getJson('/readyz');
  if (readyz.status !== 200 || typeof readyz.body !== 'object' || readyz.body === null) {
    throw new Error(`live e2e: GET ${API_URL}/readyz answered ${readyz.status}: ${JSON.stringify(readyz.body)}`);
  }
  const ready = readyz.body as { status: string; contract_version: string; mode: string; public_read_only: boolean };
  if (ready.status === 'not_ready') throw new Error(`live e2e: the API is not ready: ${JSON.stringify(readyz.body)}`);
  const served = SEMVER.exec(ready.contract_version);
  const built = SEMVER.exec(CONTRACT_VERSION);
  if (!served || !built || served[1] !== built[1] || served[2] !== built[2]) {
    throw new Error(
      `live e2e: the API serves contract ${ready.contract_version}, this UI was generated from ${CONTRACT_VERSION}. ` +
        'Regenerate the UI types (npm run gen:types) or run the API of the same branch.',
    );
  }

  const cases = await getJson('/api/demo/cases');
  if (cases.status !== 200) {
    const message = (cases.body as { message?: string } | null)?.message ?? JSON.stringify(cases.body);
    throw new Error(`live e2e: GET /api/demo/cases answered ${cases.status} (${message}): run python -m apps.api.reset first.`);
  }
  console.log(
    `live e2e: ${API_URL} serves contract ${ready.contract_version}, mode ${ready.mode}, ` +
      `${ready.public_read_only ? 'read-only public demo' : 'writable'}`,
  );
}
