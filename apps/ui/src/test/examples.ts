// The contract examples read from disk in tests (contracts/examples/api, ADR-15 section 7).
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { MockIndex } from '@/api/adapters/mockRouter';

export const EXAMPLES_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../contract-pin/examples/api');

export function readExample<T = unknown>(file: string): T {
  return JSON.parse(readFileSync(path.join(EXAMPLES_DIR, file), 'utf8')) as T;
}

export function readExampleText(file: string): string {
  return readFileSync(path.join(EXAMPLES_DIR, file), 'utf8');
}

export function exampleFiles(prefix = ''): string[] {
  return readdirSync(EXAMPLES_DIR)
    .filter((f) => f.endsWith('.json') && f.startsWith(prefix))
    .sort();
}

export const readIndex = () => readExample<MockIndex>('index.json');
