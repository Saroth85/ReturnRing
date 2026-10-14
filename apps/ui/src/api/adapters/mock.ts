// Browser mock adapter (VITE_API_MODE=mock): answers every request from contracts/examples/api
// through index.json (docs/ui-design.md 11.5). The examples are lazy chunks of mock builds only;
// live builds alias #mock-adapter to mock.disabled.ts.
import { encodeSseFrame } from '../sse';
import type { ApiAdapter, ApiRequest } from '../transport';
import { createMockRouter, type MockIndex, type MockResult } from './mockRouter';

const EXAMPLES = '../../../contract-pin/examples/api/';
const examples = import.meta.glob<unknown>('../../../contract-pin/examples/api/*.json', { import: 'default' });

export async function loadExample(file: string): Promise<unknown> {
  const loader = examples[`${EXAMPLES}${file}`];
  if (!loader) throw new Error(`mock: no example file ${file}`);
  // Deep copy: callers may not mutate the shared module value.
  return structuredClone(await loader());
}

const isTest = import.meta.env.MODE === 'test';
/** Pause between SSE frames, so streaming is visible (design 11.5: 60 ms). */
export const SSE_FRAME_DELAY_MS = isTest ? 0 : 60;
/** A small delay on JSON answers so loading states are visible in the dev server. */
const JSON_DELAY_MS = isTest ? 0 : 120;

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (ms <= 0) return resolve();
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true },
    );
  });

let routerPromise: Promise<ReturnType<typeof createMockRouter>> | null = null;
function router() {
  routerPromise ??= loadExample('index.json').then((index) => createMockRouter(index as MockIndex, loadExample));
  return routerPromise;
}

/** Turns a routed answer into the Response a real server would send. */
export function toResponse(result: MockResult, signal?: AbortSignal, frameDelayMs = SSE_FRAME_DELAY_MS): Response {
  const headers = new Headers(result.headers);
  if (result.body.kind === 'json') {
    return new Response(JSON.stringify(result.body.value), { status: result.status, headers });
  }
  if (result.body.kind === 'text') {
    return new Response(result.body.value, { status: result.status, headers });
  }
  const frames = result.body.events.map(encodeSseFrame);
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        for (const frame of frames) {
          await sleep(frameDelayMs, signal);
          controller.enqueue(encoder.encode(frame));
        }
        controller.close();
      } catch (error) {
        controller.error(error);
      }
    },
  });
  return new Response(stream, { status: result.status, headers });
}

export async function mockFetch(request: ApiRequest): Promise<Response> {
  if (request.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  const route = await router();
  const result = await route({ method: request.method, path: request.path, headers: request.headers, body: request.body });
  if (result.note && !isTest) {
    console.warn(`[mock] ${request.method} ${request.path}: ${result.note}`);
  }
  if (result.body.kind !== 'sse') await sleep(JSON_DELAY_MS, request.signal);
  return toResponse(result, request.signal);
}

export const mockAdapter: ApiAdapter | null = { kind: 'mock', fetch: mockFetch };
