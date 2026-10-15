// Vite dev-server middleware `returnring-mock` (docs/ui-design.md 11.5): serves
// contracts/examples/api over real HTTP through index.json, with the same routing table as the
// browser mock adapter (src/api/adapters/mockRouter.ts). Enabled by `VITE_API_MODE=live
// RETURNRING_API_URL=mock npm run dev:live`, so the live adapter (fetch, headers, SSE over the
// network) runs end to end without the API. Never part of a build.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import type { Plugin } from 'vite';
import { createMockRouter, type MockIndex } from '../src/api/adapters/mockRouter';
import { encodeSseFrame } from '../src/api/sse';

const SSE_FRAME_DELAY_MS = 60;

export function returnringMock({ repoRoot }: { repoRoot: string }): Plugin {
  const dir = path.join(process.env.RETURNRING_CONTRACTS_DIR ? path.resolve(process.env.RETURNRING_CONTRACTS_DIR) : path.join(repoRoot, 'contracts'), 'examples/api');
  const load = async (file: string) => {
    if (file.includes('/') || file.includes('..')) throw new Error(`mock: bad example name ${file}`);
    return JSON.parse(await readFile(path.join(dir, file), 'utf8')) as unknown;
  };
  let router: ReturnType<typeof createMockRouter> | null = null;

  return {
    name: 'returnring-mock',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = req.url ?? '/';
        if (!url.startsWith('/api/') && !url.startsWith('/healthz') && !url.startsWith('/readyz')) return next();
        try {
          router ??= createMockRouter((await load('index.json')) as MockIndex, load, { requestIdPrefix: 'mock-http' });
          const chunks: Buffer[] = [];
          for await (const chunk of req) chunks.push(chunk as Buffer);
          const headers = Object.fromEntries(
            Object.entries(req.headers).map(([k, v]) => [k, Array.isArray(v) ? v.join(', ') : (v ?? '')]),
          );
          const result = await router({
            method: req.method ?? 'GET',
            path: url,
            headers,
            body: chunks.length > 0 ? Buffer.concat(chunks).toString('utf8') : undefined,
          });
          if (result.note) server.config.logger.warn(`[returnring-mock] ${req.method} ${url}: ${result.note}`);
          res.statusCode = result.status;
          for (const [name, value] of Object.entries(result.headers)) res.setHeader(name, value);
          if (result.body.kind === 'json') {
            res.end(JSON.stringify(result.body.value));
          } else if (result.body.kind === 'text') {
            res.end(result.body.value);
          } else {
            res.setHeader('cache-control', 'no-cache');
            res.flushHeaders();
            let closed = false;
            req.on('close', () => {
              closed = true;
            });
            for (const event of result.body.events) {
              if (closed) break;
              await new Promise((resolve) => setTimeout(resolve, SSE_FRAME_DELAY_MS));
              res.write(encodeSseFrame(event));
            }
            res.end();
          }
        } catch (error) {
          next(error);
        }
      });
    },
  };
}
