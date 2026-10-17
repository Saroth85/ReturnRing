# ReturnRing UI (`apps/ui`)

The UI is a React 19 + Vite 7 + TypeScript app with Tailwind 4, React Router 7, TanStack Query,
Lucide icons, Recharts and Cytoscape.js.

The authoritative UI design is in [docs/ui-design.md](../../docs/ui-design.md). Read it together
with [CLAUDE.md](../../CLAUDE.md) before changing the UI.

## What the UI covers

- **Operator area** — queue, case review, decision history, comparison and outbox
- **Agent area** — case assistant drawer and dev/mock harness
- **Manager area** — portfolio, simulator, KPI, fairness, products, reviews and audit
- **Platform area** — shop, inspections, coupons, system, demo and about

## Run

```bash
npm ci
npm run dev
npm run dev:live
RETURNRING_API_URL=mock npm run dev:live
```

`npm run dev` uses the mock adapter and the bundled contract examples.
`npm run dev:live` talks to the API through `RETURNRING_API_URL` (default `http://127.0.0.1:8000`).

Useful URL/query switches:

- `?scenario=...` — mock-only fixture selection
- `?theme=light|dark`
- `?present=1` — presenter mode

## Service data the shell consumes

- `/readyz` — readiness, mode and clock data
- `/api/demo/cases` — live demo cases and ids
- `/api/healthz` and `/api/readyz` — operational status

The shell uses these responses to show the mode banner, top-bar time, persona landing page and
read-only notices.

## Scripts

| Script | What it does |
| --- | --- |
| `npm run gen:types` | regenerates TypeScript contract types |
| `npm run check:types-gen` | fails when generated contract files are stale |
| `npm run typecheck` | TypeScript build/typecheck |
| `npm run lint` | ESLint with import boundary rules |
| `npm test` | Vitest + Testing Library |
| `npm run test:e2e` | Playwright against the mock build |
| `npm run test:e2e:live` | Playwright against a running API |
| `npm run check:licenses` | checks runtime and dev dependency licences |
| `npm run build` | production build and bundle safety checks |
| `npm run check` | full UI validation suite |

## Live end-to-end tests

`npm run test:e2e:live` expects a running API and exercises the demo path, the read-only mode and
the golden cases.

## Layout

```text
src/api/        typed client, queries, mutations, SSE and adapters
src/app/        shell, router, persona handling and route composition
src/components/ shared UI, feedback, decision, message, charts and records
src/views/      operator, agent, manager and platform screens
tests/e2e/      Playwright specs
mock/           mock adapter and browser fixtures
```

## Docker

`Dockerfile` builds the bundle with Node and serves `dist/` with nginx.
The UI is reached on the same origin as the API through the Caddy proxy.
