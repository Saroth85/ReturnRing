#!/usr/bin/env node
// Generates src/api/contract.gen.ts: runtime constants read from the contract (ADR-15), so the
// UI never hard-codes an enum, a role or a legal number that the contract already states.
//
//   node scripts/gen-contract.mjs           write the file
//   node scripts/gen-contract.mjs --check   exit 1 when the committed file is stale
//
// Sources: contracts/json/api/*.schema.json, contracts/json/api/endpoints.json,
// contracts/examples/api/index.json (scenario names) and contracts/vocab.py (DEMO_USERS).
// Every lookup below names its path in the contract and fails loudly when the path is gone:
// a contract change then breaks this script, not forty components.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const CONTRACTS = path.resolve(ROOT, '../../contracts');
const SCHEMA_DIR = path.join(CONTRACTS, 'json/api');
const OUT = path.join(ROOT, 'src/api/contract.gen.ts');
const check = process.argv.includes('--check');

const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'));

// ---- schemas, merged by definition name ---------------------------------------------------
const defs = {};
const models = {};
for (const file of readdirSync(SCHEMA_DIR)
  .filter((f) => f.endsWith('.schema.json'))
  .sort()) {
  const schema = readJson(path.join(SCHEMA_DIR, file));
  Object.assign(defs, schema.$defs ?? {});
  const { $defs: _defs, ...top } = schema;
  defs[top.title] = top;
  models[file.slice(0, -'.schema.json'.length)] = top;
}

function resolve(schema) {
  if (schema && typeof schema.$ref === 'string') {
    const name = schema.$ref.replace('#/$defs/', '');
    if (!defs[name]) throw new Error(`unresolved $ref ${schema.$ref}`);
    return resolve(defs[name]);
  }
  return schema;
}

function at(defName, propertyPath) {
  let node = defs[defName];
  if (!node) throw new Error(`contract: no definition ${defName}`);
  for (const key of propertyPath.split('.')) {
    node = resolve(node);
    if (!node || !(key in node)) throw new Error(`contract: ${defName}.${propertyPath} not found (at ${key})`);
    node = node[key];
  }
  return resolve(node);
}

/** The string values of an enum schema, through $ref and nullable anyOf. */
function enumOf(schema, where) {
  const s = resolve(schema);
  if (Array.isArray(s.enum)) return s.enum;
  if (typeof s.const === 'string') return [s.const];
  for (const branch of s.anyOf ?? s.oneOf ?? []) {
    const r = resolve(branch);
    if (Array.isArray(r.enum)) return r.enum;
  }
  throw new Error(`contract: ${where} is not an enum`);
}

const enumAt = (defName, propertyPath) => enumOf(at(defName, `properties.${propertyPath}`), `${defName}.${propertyPath}`);

/** The minimum of the return window from the lever catalogue rule (if id is return_window_days). */
function legalMinimumWindowDays() {
  for (const rule of defs.Lever?.allOf ?? []) {
    if (rule.if?.properties?.id?.const === 'return_window_days') {
      const min = rule.then?.properties?.value?.minimum;
      if (typeof min === 'number') return min;
    }
  }
  throw new Error('contract: Lever rule for return_window_days has no minimum');
}

/** Query parameters of a query model: their kind and default, for the client and the mock. */
function queryParams(stem) {
  const model = models[stem];
  if (!model) throw new Error(`contract: no query model ${stem}`);
  const out = {};
  for (const [name, raw] of Object.entries(model.properties ?? {})) {
    const s = resolve(raw);
    const branches = (s.anyOf ?? [s]).map(resolve).filter((b) => b.type !== 'null');
    const first = branches[0] ?? {};
    let kind = 'string';
    if (first.type === 'array') kind = 'array';
    else if (first.type === 'integer' || first.type === 'number') kind = 'number';
    else if (first.type === 'boolean') kind = 'boolean';
    out[name] = { kind, default: 'default' in s ? s.default : null };
  }
  return out;
}

const ACTOR_FIELD_NAMES = ['approver', 'author', 'requested_by', 'asked_by', 'inspector', 'approved_by'];

// ---- endpoints, scenarios, demo users -----------------------------------------------------
const endpointsFile = readJson(path.join(SCHEMA_DIR, 'endpoints.json'));
const endpoints = endpointsFile.endpoints.map((e) => {
  const request = e.request ? models[e.request] : null;
  if (e.request && !request) throw new Error(`contract: endpoint ${e.method} ${e.path} names unknown ${e.request}`);
  const actorFields = request ? ACTOR_FIELD_NAMES.filter((f) => f in (request.properties ?? {})) : [];
  return {
    method: e.method,
    path: e.path,
    summary: e.summary,
    stories: e.stories,
    query: e.query,
    request: e.request,
    response: e.response,
    status: e.status,
    mediaType: e.media_type,
    altMediaTypes: e.alt_media_types,
    errors: e.errors,
    roles: e.roles,
    actorField: actorFields[0] ?? null,
  };
});

const index = readJson(path.join(CONTRACTS, 'examples/api/index.json'));
const scenarios = [...new Set(index.routes.map((r) => r.scenario))].sort((a, b) =>
  a === 'default' ? -1 : b === 'default' ? 1 : a.localeCompare(b),
);

const vocab = readFileSync(path.join(CONTRACTS, 'vocab.py'), 'utf8');
const usersMatch = vocab.match(/^DEMO_USERS:[^=]*=\s*\{([^}]*)\}/m);
if (!usersMatch) throw new Error('contracts/vocab.py: DEMO_USERS not found');
const demoUsers = [...usersMatch[1].matchAll(/"([a-z][a-z0-9._-]*)"\s*:\s*"([a-z]+)"/g)].map(([, id, role]) => ({ id, role }));
if (demoUsers.length === 0) throw new Error('contracts/vocab.py: DEMO_USERS is empty');

// ---- output -------------------------------------------------------------------------------
const constants = [
  ['CONTRACT_VERSION', 'endpoints.json contract_version', endpointsFile.contract_version],
  ['QUADRANTS', 'Quadrant', enumOf(defs.Quadrant, 'Quadrant')],
  ['PATHS', 'Path', enumOf(defs.Path, 'Path')],
  ['EVIDENCE_QUALITIES', 'EvidenceQuality', enumOf(defs.EvidenceQuality, 'EvidenceQuality')],
  ['RETURN_REASONS', 'ReturnReason', enumOf(defs.ReturnReason, 'ReturnReason')],
  ['DECISION_ACTIONS', 'DecisionRequest.action', enumAt('DecisionRequest', 'action')],
  ['OVERRIDE_CATEGORIES', 'DecisionRequest.override_category', enumAt('DecisionRequest', 'override_category')],
  ['DECISION_STATES', 'DecisionView.state', enumAt('DecisionView', 'state')],
  ['REVISION_TRIGGERS', 'DecisionView.revision_trigger', enumAt('DecisionView', 'revision_trigger')],
  ['EVIDENCE_KINDS', 'EvidenceRequest.kind', enumAt('EvidenceRequest', 'kind')],
  ['AUDIT_ACTIONS', 'AuditEntry.action', enumAt('AuditEntry', 'action')],
  ['LEVER_IDS', 'Lever.id', enumAt('Lever', 'id')],
  ['LEVER_SCOPES', 'Lever.scope', enumAt('Lever', 'scope')],
  ['METRIC_UNITS', 'Metric.unit', enumAt('Metric', 'unit')],
  ['NATURES', 'Provenance.nature', enumAt('Provenance', 'nature')],
  ['MESSAGE_LANGS', 'CustomerMessageView.lang', enumAt('CustomerMessageView', 'lang')],
  ['LEGAL_CLAUSE_KINDS', 'CustomerMessageView.legal_clause_kind', enumAt('CustomerMessageView', 'legal_clause_kind')],
  ['DRAFT_TONES', 'DraftView.tone', enumAt('DraftView', 'tone')],
  ['STATEMENT_LABELS', 'ExplanationStatement.label', enumAt('ExplanationStatement', 'label')],
  ['GENERATION_PATHS', 'GenerationInfo.generation_path', enumAt('GenerationInfo', 'generation_path')],
  ['AGENT_TOOLS', 'ToolCallEvent.tool', enumAt('ToolCallEvent', 'tool')],
  ['ENTITY_REF_KINDS', 'EntityRef.kind', enumAt('EntityRef', 'kind')],
  ['AGENT_MAX_STEPS', 'ToolCallEvent.step maximum', at('ToolCallEvent', 'properties.step').maximum],
  ['LEGAL_MINIMUM_WINDOW_DAYS', 'Lever rule: return_window_days minimum', legalMinimumWindowDays()],
  ['MOCK_SCENARIOS', 'contracts/examples/api/index.json scenarios', scenarios],
  ['DEMO_USERS', 'contracts/vocab.py DEMO_USERS', demoUsers],
];
for (const [name, source, value] of constants) {
  if (value === undefined || value === null) throw new Error(`contract: ${name} (${source}) is missing`);
}

const queryModels = ['queue_query', 'audit_query', 'kpi_query', 'outbox_query', 'anomalies_query'];
const queryParamsOut = Object.fromEntries(queryModels.map((stem) => [stem, queryParams(stem)]));

const lines = [
  '/* eslint-disable */',
  '// Generated by scripts/gen-contract.mjs from the contract (ADR-15): schemas, endpoints.json,',
  '// examples/api/index.json and contracts/vocab.py. Do not edit: run `npm run gen:types`.',
  '',
];
for (const [name, source, value] of constants) {
  lines.push(`/** From ${source}. */`);
  lines.push(`export const ${name} = ${JSON.stringify(value, null, 2)} as const;`);
  lines.push('');
}
lines.push('/** Query parameters of each query model: kind and default (contracts/json/api/<model>.schema.json). */');
lines.push(`export const QUERY_PARAMS = ${JSON.stringify(queryParamsOut, null, 2)} as const;`);
lines.push('');
lines.push('/** endpoints.json: every route of ADR-15 with its roles, statuses and the actor field of its body. */');
lines.push(`export const ENDPOINTS = ${JSON.stringify(endpoints, null, 2)} as const;`);
lines.push('');
const output = `${lines.join('\n')}`;

if (check) {
  let current = '';
  try {
    current = readFileSync(OUT, 'utf8');
  } catch {
    // missing file: stale
  }
  if (current !== output) {
    console.error('src/api/contract.gen.ts is stale: run `npm run gen:types` and commit the result');
    process.exit(1);
  }
  console.log('contract.gen.ts is up to date');
} else {
  writeFileSync(OUT, output);
  console.log(`wrote ${path.relative(ROOT, OUT)}`);
}
