#!/usr/bin/env node
// Fails when the production bundle (dist/) contains the contract examples or the mock adapter:
// examples are bundled only in mock builds (`npm run build:mock`, docs/ui-design.md 11.5).
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, process.argv[2] ?? 'dist');
const MARKERS = ['contracts/examples/api', 'build_api_examples', 'returnring-mock', 'mock: no example file'];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? files(full) : [full];
  });
}

const found = [];
for (const file of files(DIST)) {
  if (!/\.(js|css|html|json)$/.test(file)) continue;
  const text = readFileSync(file, 'utf8');
  for (const marker of MARKERS) {
    if (text.includes(marker)) found.push(`${path.relative(ROOT, file)}: "${marker}"`);
  }
}
if (found.length > 0) {
  for (const line of found) console.error(`mock content in the production bundle: ${line}`);
  process.exit(1);
}
console.log(`bundle OK: no contract example or mock adapter in ${path.relative(ROOT, DIST)}/`);
