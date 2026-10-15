#!/usr/bin/env node
// Fails when a production bundle (dist/ by default) contains the contract examples, the mock
// adapter or the dev-only agent harness: examples are bundled only in mock builds
// (`npm run build:mock`, docs/ui-design.md 11.5).
//
//   node scripts/check-bundle.mjs [distDir]
//
// Three checks, over every file of the bundle including the source maps (.map files are deployed):
// 1. markers that only the mock code carries, in the generated files;
// 2. the module list of each source map: no example JSON, mock adapter or dev harness module;
// 3. when contracts/examples/api is reachable (a repo checkout, not the Docker build context):
//    every distinctive string of the examples that the app's own sources do not contain. An
//    example imported directly (import x from '.../case_card.marco.json') is inlined as object
//    literals that no marker matches, but its display names and origins still show up here.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Strings only the mock adapter, the mock middleware and the example builder print. */
export const MARKERS = ['build_api_examples', 'returnring-mock', 'mock: no example file', 'x-mock-example'];

/** Modules that must never reach a production bundle (matched against source map `sources`). */
export const FORBIDDEN_MODULES = [
  /contracts\/examples\//,
  /src\/api\/adapters\/mock\.ts$/,
  /src\/api\/adapters\/mockRouter\.ts$/,
  /mock\/returnring-mock\.ts$/,
  /src\/views\/agent\/dev\//,
];

const TEXT = /\.(js|mjs|css|html|json|map|svg|txt|webmanifest)$/;
/** Example strings shorter than this are too generic to prove anything (ids, enum values). */
const MIN_EXAMPLE_STRING = 12;

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? files(full) : [full];
  });
}

/** Distinctive string leaves of the example JSON files. */
export function exampleStrings(examplesDir) {
  const out = new Set();
  const walk = (value) => {
    if (typeof value === 'string') {
      if (value.length >= MIN_EXAMPLE_STRING && /\s/.test(value)) out.add(value);
    } else if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') Object.values(value).forEach(walk);
  };
  for (const name of readdirSync(examplesDir)) {
    if (name.endsWith('.json')) walk(JSON.parse(readFileSync(path.join(examplesDir, name), 'utf8')));
  }
  return out;
}

/** The text of the app's own sources (not tests): strings found there may legitimately ship. */
export function appSourceText(srcDir) {
  const parts = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== 'test') walk(full);
      } else if (/\.(ts|tsx|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
        parts.push(readFileSync(full, 'utf8'));
      }
    }
  };
  walk(srcDir);
  return parts.join('\n');
}

/**
 * Scans a bundle directory. Returns one line per finding (empty when clean).
 * `examplesDir` and `srcDir` are optional: without them the example-value check is skipped.
 */
export function scanBundle({ dist, examplesDir = null, srcDir = null }) {
  const found = [];
  const texts = [];
  for (const file of files(dist)) {
    if (!TEXT.test(file)) continue;
    const rel = path.relative(dist, file);
    const text = readFileSync(file, 'utf8');
    texts.push(text);
    if (file.endsWith('.map')) {
      let map;
      try {
        map = JSON.parse(text);
      } catch {
        found.push(`${rel}: not a JSON source map`);
        continue;
      }
      for (const source of map.sources ?? []) {
        const normalised = String(source).replace(/\\/g, '/');
        if (FORBIDDEN_MODULES.some((re) => re.test(normalised))) found.push(`${rel}: bundles module ${normalised}`);
      }
      // The generated code is checked through its own file; the map's sourcesContent holds
      // comments of the app's sources, which may name the markers.
      continue;
    }
    for (const marker of MARKERS) {
      if (text.includes(marker)) found.push(`${rel}: "${marker}"`);
    }
  }

  if (examplesDir && srcDir && existsSync(examplesDir)) {
    const own = appSourceText(srcDir);
    const haystack = texts.join('\n');
    for (const value of exampleStrings(examplesDir)) {
      if (own.includes(value)) continue;
      const escaped = JSON.stringify(value).slice(1, -1);
      if (haystack.includes(value) || (escaped !== value && haystack.includes(escaped))) {
        found.push(`example value in the bundle: "${value.length > 80 ? `${value.slice(0, 77)}...` : value}"`);
      }
    }
  }
  return found;
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  const dist = path.resolve(ROOT, process.argv[2] ?? 'dist');
  const contracts = process.env.RETURNRING_CONTRACTS_DIR || path.resolve(ROOT, '../../contracts');
  const examplesDir = path.resolve(contracts, 'examples/api');
  const hasExamples = existsSync(examplesDir);
  const found = scanBundle({ dist, examplesDir: hasExamples ? examplesDir : null, srcDir: path.join(ROOT, 'src') });
  if (found.length > 0) {
    for (const line of found) console.error(`mock content in the production bundle: ${line}`);
    process.exit(1);
  }
  const scope = hasExamples ? 'markers, source map modules and example values' : 'markers and source map modules (no examples here)';
  console.log(`bundle OK: no contract example or mock adapter in ${path.relative(ROOT, dist) || dist}/ (${scope}, .map files included)`);
}
