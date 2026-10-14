// The UI compares the contract version the API reports with the one its types were generated from
// (UI1): a different major or minor version is shown, never silently absorbed.
import { describe, expect, it } from 'vitest';
import { CONTRACT_VERSION } from './contract.gen';
import { compareContract, contractVersionOf } from './contractVersion';

describe('compareContract', () => {
  it('matches the same version and tolerates a patch difference', () => {
    expect(compareContract('1.1.0', '1.1.0')).toEqual({ status: 'same', server: '1.1.0', ui: '1.1.0' });
    expect(compareContract('1.1.3', '1.1.0').status).toBe('patch');
  });

  it('flags a different minor or major version', () => {
    expect(compareContract('1.4.0', '1.1.0')).toEqual({ status: 'mismatch', server: '1.4.0', ui: '1.1.0' });
    expect(compareContract('2.0.0', '1.1.0').status).toBe('mismatch');
    expect(compareContract('1.0.9', '1.1.0').status).toBe('mismatch');
  });

  it('is unknown without a readable version', () => {
    expect(compareContract(null, '1.1.0')).toEqual({ status: 'unknown', server: null, ui: '1.1.0' });
    expect(compareContract('latest', '1.1.0').status).toBe('unknown');
  });

  it('compares with the generated version by default', () => {
    expect(compareContract(CONTRACT_VERSION).status).toBe('same');
  });
});

describe('contractVersionOf', () => {
  it('reads contract_version from any API body that carries it (readyz, healthz)', () => {
    expect(contractVersionOf({ status: 'ready', contract_version: '1.4.0' })).toBe('1.4.0');
    expect(contractVersionOf({ status: 'ok' })).toBeNull();
    expect(contractVersionOf(undefined)).toBeNull();
    expect(contractVersionOf({ contract_version: 14 })).toBeNull();
  });
});
