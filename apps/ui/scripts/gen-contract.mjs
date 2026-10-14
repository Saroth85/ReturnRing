#!/usr/bin/env node
// Generates src/api/contract.gen.ts: runtime constants read from the contract (ADR-15), so the
// UI never hard-codes an enum, a role or a legal number that the contract already states.
//
//   node scripts/gen-contract.mjs           write the file
//   node scripts/gen-contract.mjs --check   exit 1 when the committed file is stale
//
// Sources: contracts/json/api/*.schema.json, contracts/json/api/endpoints.json,
// contracts/examples/api/index.json (scenario names) and contracts/vocab.py (DEMO_USERS, the
// human-only and reason-required audit actions, MIN_REASON_CHARS).
// Every lookup below names its path in the contract and fails loudly when the path is gone:
// a contract change then breaks this script, not forty components.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// RETURNRING_CONTRACTS_DIR points the generator at another checkout's contracts/ (to try a
// pending contract version before it lands); the default is the repository's own.
// Pinned contract (contract-pin/README.md) until the UI migrates to the API's current contract.
const CONTRACTS = path.resolve(process.env.RETURNRING_CONTRACTS_DIR || path.resolve(ROOT, 'contract-pin'));
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

const hasProperty = (defName, property) => Boolean(defs[defName]?.properties && property in defs[defName].properties);

/** The first of several contract paths that exists, for a value a later contract moved. */
function enumAtFirst(...paths) {
  for (const [defName, propertyPath] of paths) {
    if (hasProperty(defName, propertyPath)) return enumAt(defName, propertyPath);
  }
  throw new Error(`contract: none of ${paths.map((p) => p.join('.')).join(', ')} exists`);
}

/**
 * The lever schema: the `Lever` definition when the contract names one (1.1.0), otherwise the
 * items of a `levers` array (later contracts inline the lever into each list).
 */
function leverSchema() {
  if (defs.Lever) return resolve(defs.Lever);
  for (const name of Object.keys(defs).sort()) {
    const levers = defs[name]?.properties?.levers;
    if (!levers) continue;
    for (const branch of [levers, ...(levers.anyOf ?? [])].map(resolve)) {
      const items = branch?.items ? resolve(branch.items) : null;
      if (items?.properties?.id && items?.properties?.scope) return items;
    }
  }
  throw new Error('contract: no lever schema (neither a Lever definition nor a levers array)');
}
const LEVER = leverSchema();

/** The minimum of the return window from the lever catalogue rule (if id is return_window_days). */
function legalMinimumWindowDays() {
  for (const rule of LEVER.allOf ?? []) {
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

/** A frozenset of audit actions in contracts/vocab.py, checked against AuditEntry.action. */
function vocabActionSet(name, auditActions) {
  const match = vocab.match(new RegExp(`^${name}:[^=]*=\\s*frozenset\\(\\s*\\{([^}]*)\\}`, 'm'));
  if (!match) throw new Error(`contracts/vocab.py: ${name} not found`);
  const values = [...match[1].matchAll(/"([a-z_]+)"/g)].map(([, v]) => v).sort();
  if (values.length === 0) throw new Error(`contracts/vocab.py: ${name} is empty`);
  for (const v of values) if (!auditActions.includes(v)) throw new Error(`contracts/vocab.py: ${name} has unknown action ${v}`);
  return values;
}
const minReasonMatch = vocab.match(/^MIN_REASON_CHARS\s*(?::[^=]*)?=\s*(\d+)/m);
if (!minReasonMatch) throw new Error('contracts/vocab.py: MIN_REASON_CHARS not found');
const auditActions = enumAt('AuditEntry', 'action');

// ---- output -------------------------------------------------------------------------------
// An entry with a fourth element `{ optional: true }` is left out of the file when the contract no
// longer states it (DraftView.tone is gone after 1.1.0); every other entry must resolve.
const constants = [
  ['CONTRACT_VERSION', 'endpoints.json contract_version', endpointsFile.contract_version],
  ['QUADRANTS', 'Quadrant', enumOf(defs.Quadrant, 'Quadrant')],
  ['PATHS', 'Path', enumOf(defs.Path, 'Path')],
  ['EVIDENCE_QUALITIES', 'EvidenceQuality', enumOf(defs.EvidenceQuality, 'EvidenceQuality')],
  ['RETURN_REASONS', 'ReturnReason', enumOf(defs.ReturnReason, 'ReturnReason')],
  ['DECISION_ACTIONS', 'DecisionRequest.action', enumAt('DecisionRequest', 'action')],
  ['OVERRIDE_CATEGORIES', 'DecisionRequest.override_category', enumAt('DecisionRequest', 'override_category')],
  ['DECISION_STATES', 'DecisionView.state', enumAt('DecisionView', 'state')],
  ['QUEUE_SORTS', 'QueueQuery.sort', enumAt('QueueQuery', 'sort')],
  ['REVISION_TRIGGERS', 'DecisionView.revision_trigger', enumAt('DecisionView', 'revision_trigger')],
  ['EVIDENCE_KINDS', 'EvidenceRequest.kind', enumAt('EvidenceRequest', 'kind')],
  ['AUDIT_ACTIONS', 'AuditEntry.action', auditActions],
  ['LEVER_IDS', 'Lever.id', enumOf(LEVER.properties.id, 'Lever.id')],
  ['LEVER_SCOPES', 'Lever.scope', enumOf(LEVER.properties.scope, 'Lever.scope')],
  ['METRIC_UNITS', 'Metric.unit', enumAt('Metric', 'unit')],
  ['NATURES', 'Provenance.nature', enumAt('Provenance', 'nature')],
  ['MESSAGE_LANGS', 'CustomerMessageView.lang', enumAtFirst(['CustomerMessageView', 'lang'], ['MessageChoice', 'lang'])],
  ['LEGAL_CLAUSE_KINDS', 'CustomerMessageView.legal_clause_kind', enumAt('CustomerMessageView', 'legal_clause_kind')],
  ['DRAFT_TONES', 'DraftView.tone', hasProperty('DraftView', 'tone') ? enumAt('DraftView', 'tone') : undefined, { optional: true }],
  ['STATEMENT_LABELS', 'ExplanationStatement.label', enumAt('ExplanationStatement', 'label')],
  ['GENERATION_PATHS', 'GenerationInfo.generation_path', enumAt('GenerationInfo', 'generation_path')],
  ['AGENT_TOOLS', 'ToolCallEvent.tool', enumAt('ToolCallEvent', 'tool')],
  ['ENTITY_REF_KINDS', 'EntityRef.kind', enumAt('EntityRef', 'kind')],
  ['AGENT_MAX_STEPS', 'ToolCallEvent.step maximum', at('ToolCallEvent', 'properties.step').maximum],
  ['AGENT_QUESTION_MAX_LENGTH', 'AgentAskRequest.question maxLength', at('AgentAskRequest', 'properties.question').maxLength],
  ['PHOTO_BASE64_MAX_LENGTH', 'PhotoUpload.data_base64 maxLength', at('PhotoUpload', 'properties.data_base64').maxLength],
  ['HUMAN_ONLY_ACTIONS', 'contracts/vocab.py HUMAN_ONLY_ACTIONS', vocabActionSet('HUMAN_ONLY_ACTIONS', auditActions)],
  ['REASON_REQUIRED_ACTIONS', 'contracts/vocab.py REASON_REQUIRED_ACTIONS', vocabActionSet('REASON_REQUIRED_ACTIONS', auditActions)],
  ['MIN_REASON_CHARS', 'contracts/vocab.py MIN_REASON_CHARS', Number(minReasonMatch[1])],
  ['LEGAL_MINIMUM_WINDOW_DAYS', 'Lever rule: return_window_days minimum', legalMinimumWindowDays()],
  ['MOCK_SCENARIOS', 'contracts/examples/api/index.json scenarios', scenarios],
  ['DEMO_USERS', 'contracts/vocab.py DEMO_USERS', demoUsers],
];
for (const [name, source, value, options] of constants) {
  if ((value === undefined || value === null) && !options?.optional) throw new Error(`contract: ${name} (${source}) is missing`);
}
const stated = constants.filter(([, , value]) => value !== undefined && value !== null);

const queryModels = ['queue_query', 'audit_query', 'kpi_query', 'outbox_query', 'anomalies_query'];
const queryParamsOut = Object.fromEntries(queryModels.map((stem) => [stem, queryParams(stem)]));

const lines = [
  '/* eslint-disable */',
  '// Generated by scripts/gen-contract.mjs from the contract (ADR-15): schemas, endpoints.json,',
  '// examples/api/index.json and contracts/vocab.py. Do not edit: run `npm run gen:types`.',
  '',
];
for (const [name, source, value] of stated) {
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
