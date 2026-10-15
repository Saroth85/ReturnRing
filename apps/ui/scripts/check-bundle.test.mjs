import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { scanBundle } from './check-bundle.mjs';

let dir;
let dist;
let examples;
let src;

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'check-bundle-'));
  dist = path.join(dir, 'dist');
  examples = path.join(dir, 'examples');
  src = path.join(dir, 'src');
  for (const d of [path.join(dist, 'assets'), examples, src]) mkdirSync(d, { recursive: true });
  writeFileSync(
    path.join(examples, 'case_card.marco.json'),
    JSON.stringify({ display_name: 'Marco Bianchi', origin: 'Contract example: risk model on the synthetic shop', id: 'RET-000102' }),
  );
  // The app's own copy names the beat persona: shipping it is fine.
  writeFileSync(path.join(src, 'copy.ts'), "export const PERSONA = 'Marco Bianchi';\n");
  writeFileSync(path.join(dist, 'index.html'), '<!doctype html><script src="/assets/index.js"></script>');
  writeFileSync(path.join(dist, 'assets', 'index.js'), 'const a={display_name:"Marco Bianchi"};');
});

afterEach(() => rmSync(dir, { recursive: true, force: true }));

const scan = () => scanBundle({ dist, examplesDir: examples, srcDir: src });

describe('check-bundle (UI40)', () => {
  it('passes a clean bundle whose strings come from the app sources', () => {
    expect(scan()).toEqual([]);
  });

  it('finds an example inlined by a direct JSON import', () => {
    writeFileSync(path.join(dist, 'assets', 'About.js'), 'const m={origin:"Contract example: risk model on the synthetic shop"};');
    expect(scan()).toEqual([expect.stringMatching(/example value in the bundle: "Contract example: risk model/)]);
  });

  it('scans the source maps: example modules in sources and example values in sourcesContent', () => {
    const map = {
      version: 3,
      sources: ['../../../../contracts/examples/api/case_card.marco.json', '../src/views/About.tsx'],
      sourcesContent: ['{"origin": "Contract example: risk model on the synthetic shop"}', ''],
      mappings: '',
    };
    writeFileSync(path.join(dist, 'assets', 'About.js.map'), JSON.stringify(map));
    const found = scan();
    expect(found.some((f) => /About\.js\.map: bundles module .*contracts\/examples\/api\/case_card\.marco\.json/.test(f))).toBe(true);
    expect(found.some((f) => /example value in the bundle/.test(f))).toBe(true);
  });

  it('finds the mock adapter by its markers and by its module', () => {
    writeFileSync(path.join(dist, 'assets', 'mock.js'), 'throw new Error(`mock: no example file ${f}`)');
    writeFileSync(path.join(dist, 'assets', 'mock.js.map'), JSON.stringify({ version: 3, sources: ['../src/api/adapters/mockRouter.ts'], mappings: '' }));
    const found = scan();
    expect(found).toContain('assets/mock.js: "mock: no example file"');
    expect(found).toContain('assets/mock.js.map: bundles module ../src/api/adapters/mockRouter.ts');
  });

  it('finds the mock scenario store and its header (they must be dead code in a live build)', () => {
    writeFileSync(path.join(dist, 'assets', 'client.js'), 'if(s!=="default")h["X-Mock-Scenario"]=s;g("returnring.mockScenario");');
    const found = scan();
    expect(found).toContain('assets/client.js: "X-Mock-Scenario"');
    expect(found).toContain('assets/client.js: "returnring.mockScenario"');
  });

  it('runs without the examples (Docker build context) on markers and modules only', () => {
    writeFileSync(path.join(dist, 'assets', 'About.js'), 'const m={origin:"Contract example: risk model on the synthetic shop"};');
    expect(scanBundle({ dist })).toEqual([]);
  });
});
