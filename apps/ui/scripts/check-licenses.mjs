#!/usr/bin/env node
// Licence gate for the UI (CLAUDE.md rule 15, ADR-12, LIC-01). No dependency: it walks the
// package.json files under node_modules.
//
// - Runtime tree: package.json "dependencies", followed transitively through each installed
//   package's dependencies, optionalDependencies and installed peerDependencies. This is what can
//   end up in the bundle, so it must be MIT, BSD, Apache-2.0 or ISC.
// - Dev tree: every other installed package. Dev tools are never distributed, so they may also be
//   PSF or PostgreSQL (rule 15), MPL-2.0 (as for the Python dev tools) or CC-BY-4.0 (data).
// - Never GPL, AGPL or LGPL, and never an unknown or missing licence.
//
// Usage: node scripts/check-licenses.mjs [--verbose]

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NODE_MODULES = path.join(ROOT, 'node_modules');
const verbose = process.argv.includes('--verbose');

// SPDX ids by family. 0BSD is the zero-clause BSD licence: the Python gate also counts it as BSD.
const BSD = ['BSD-2-Clause', 'BSD-3-Clause', '0BSD'];
const RUNTIME_ALLOWED = new Set(['MIT', ...BSD, 'Apache-2.0', 'ISC']);
const DEV_ALLOWED = new Set([
  ...RUNTIME_ALLOWED,
  'Python-2.0', // the PSF licence (rule 15)
  'PSF-2.0',
  'PostgreSQL',
  'MPL-2.0',
  'CC-BY-4.0',
]);
// Dev-only data packages under a licence outside the lists above, each named with its reason.
// Nothing here may reach the runtime tree: the check fails if one of them does.
const DEV_DATA_EXCEPTIONS = new Map([
  [
    'language-subtag-registry',
    {
      licence: 'CC0-1.0',
      reason:
        'IANA language subtag registry as JSON data, public domain dedication; pulled by ' +
        'eslint-plugin-jsx-a11y (language-tags) for the lang attribute rule; lint only, never bundled',
    },
  ],
]);
// Licences written in a non-SPDX way by some packages, mapped to their SPDX id.
const ALIASES = new Map([
  ['Apache 2.0', 'Apache-2.0'],
  ['Apache License 2.0', 'Apache-2.0'],
  ['Apache2', 'Apache-2.0'],
  ['BSD', 'BSD-3-Clause'],
  ['MIT License', 'MIT'],
]);

function readJson(file) {
  return JSON.parse(readFileSync(file, 'utf8'));
}

/** The licence of a package.json as an SPDX expression, or null when missing. */
function licenceOf(pkg) {
  if (typeof pkg.license === 'string') return pkg.license;
  if (pkg.license && typeof pkg.license.type === 'string') return pkg.license.type;
  if (Array.isArray(pkg.licenses) && pkg.licenses.length > 0) {
    const ids = pkg.licenses.map((l) => (typeof l === 'string' ? l : l.type));
    return ids.length === 1 ? ids[0] : `(${ids.join(' OR ')})`;
  }
  return null;
}

/**
 * Whether an SPDX expression is satisfied by the allowed set: OR needs one allowed branch, AND
 * needs every branch, WITH exceptions are not accepted, '+' suffixes are kept as written.
 */
export function satisfies(expression, allowed) {
  // Multi-word aliases ("Apache 2.0") are replaced in the text; single tokens in primary().
  let normalised = expression;
  for (const [alias, id] of ALIASES) if (alias.includes(' ')) normalised = normalised.split(alias).join(id);
  const tokens = normalised.replace(/\(/g, ' ( ').replace(/\)/g, ' ) ').trim().split(/\s+/);
  let pos = 0;
  function primary() {
    const token = tokens[pos++];
    if (token === '(') {
      const value = orExpr();
      if (tokens[pos++] !== ')') throw new Error(`unbalanced licence expression: ${expression}`);
      return value;
    }
    if (token === undefined || token === ')') throw new Error(`bad licence expression: ${expression}`);
    const id = ALIASES.get(token) ?? token;
    return allowed.has(id);
  }
  function andExpr() {
    let value = primary();
    while (tokens[pos] === 'AND') {
      pos++;
      value = primary() && value;
    }
    return value;
  }
  function orExpr() {
    let value = andExpr();
    while (tokens[pos] === 'OR') {
      pos++;
      value = andExpr() || value;
    }
    return value;
  }
  if (/\bWITH\b/.test(expression)) return false;
  const result = orExpr();
  if (pos !== tokens.length) throw new Error(`bad licence expression: ${expression}`);
  return result;
}

/** Every installed package directory, scoped packages and nested node_modules included. */
function installedPackages(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
    if (entry.name.startsWith('@')) {
      installedPackages(full, out);
      continue;
    }
    if (existsSync(path.join(full, 'package.json'))) {
      out.push(full);
      installedPackages(path.join(full, 'node_modules'), out);
    }
  }
  return out;
}

/** Node resolution: the nearest node_modules/<name> walking up from the requiring package. */
function resolveFrom(fromDir, name) {
  let dir = fromDir;
  for (;;) {
    const candidate = path.join(dir, 'node_modules', name);
    if (existsSync(path.join(candidate, 'package.json'))) return candidate;
    if (dir === ROOT || dir === path.dirname(dir)) return null;
    dir = path.dirname(dir);
    if (path.basename(dir) === 'node_modules') dir = path.dirname(dir);
  }
}

function main() {
  if (!existsSync(NODE_MODULES)) {
    console.error('node_modules is missing: run npm ci first');
    return 1;
  }
  const rootPkg = readJson(path.join(ROOT, 'package.json'));

  // Runtime tree: breadth-first from the root "dependencies".
  const runtime = new Set();
  const queue = Object.keys(rootPkg.dependencies ?? {}).map((name) => ({ from: ROOT, name, optional: false }));
  const missing = [];
  while (queue.length > 0) {
    const { from, name, optional } = queue.shift();
    const dir = resolveFrom(from, name);
    if (dir === null) {
      if (!optional) missing.push(`${name} (required by ${path.relative(ROOT, from) || 'package.json'})`);
      continue;
    }
    if (runtime.has(dir)) continue;
    runtime.add(dir);
    const pkg = readJson(path.join(dir, 'package.json'));
    for (const dep of Object.keys(pkg.dependencies ?? {})) queue.push({ from: dir, name: dep, optional: false });
    for (const dep of Object.keys(pkg.optionalDependencies ?? {})) queue.push({ from: dir, name: dep, optional: true });
    // A peer is provided by someone else, but if it is installed it ships in the bundle too.
    for (const dep of Object.keys(pkg.peerDependencies ?? {})) queue.push({ from: dir, name: dep, optional: true });
  }

  const failures = [];
  const counts = { runtime: 0, dev: 0 };
  for (const dir of installedPackages(NODE_MODULES)) {
    const pkg = readJson(path.join(dir, 'package.json'));
    // Nested package.json files that only set "type" or "sideEffects" for a sub-folder are not packages.
    if (!pkg.name || !pkg.version) continue;
    const tree = runtime.has(dir) ? 'runtime' : 'dev';
    counts[tree]++;
    const licence = licenceOf(pkg);
    const label = `${tree} ${pkg.name}@${pkg.version}`;
    if (licence === null) {
      failures.push(`${label}: no licence declared`);
      continue;
    }
    if (/GPL/i.test(licence)) {
      failures.push(`${label}: ${licence} (GPL family is never allowed)`);
      continue;
    }
    const exception = DEV_DATA_EXCEPTIONS.get(pkg.name);
    if (exception && exception.licence === licence) {
      if (tree === 'runtime') failures.push(`${label}: ${licence} is a dev-only exception but is in the runtime tree`);
      else if (verbose) console.log(`ok ${label}: ${licence} (dev data exception: ${exception.reason})`);
      continue;
    }
    let ok;
    try {
      ok = satisfies(licence, tree === 'runtime' ? RUNTIME_ALLOWED : DEV_ALLOWED);
    } catch (err) {
      failures.push(`${label}: ${err.message}`);
      continue;
    }
    if (!ok) failures.push(`${label}: ${licence} not allowed for the ${tree} tree`);
    else if (verbose) console.log(`ok ${label}: ${licence}`);
  }
  for (const name of missing) failures.push(`runtime ${name}: not installed`);

  for (const line of failures) console.error(`licence not allowed: ${line}`);
  console.log(
    failures.length === 0
      ? `licences OK: ${counts.runtime} runtime packages (MIT, BSD, Apache-2.0, ISC), ${counts.dev} dev packages`
      : `${failures.length} licence problem(s)`,
  );
  return failures.length === 0 ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(main());
}
