# ReturnRing UI (`apps/ui`)

React 19 + Vite 7 + TypeScript (strict) + Tailwind 4 + React Router 7 + TanStack Query, with
Cytoscape.js and Recharts installed for the graph and the charts. The design, the screens, the
shared components and the ownership of every directory are in [docs/ui-design.md](../../docs/ui-design.md)
(section 13 and the appendix). Read it, ADR-15 and CLAUDE.md before building.

## Run

```bash
npm ci                 # Node 22, npm 10; exact versions from package-lock.json
npm run dev            # http://localhost:5173 on the contract examples (mock adapter)
npm run dev:live       # live adapter; /api, /healthz, /readyz proxied to RETURNRING_API_URL
                       # (default http://127.0.0.1:8000)
RETURNRING_API_URL=mock npm run dev:live   # live adapter against the examples served over HTTP
```

- `?scenario=cached` (or the scenario switcher in the mode banner) sets the mock scenario: the
  scenarios of `contracts/examples/api/index.json` (`default`, `cached`, `conflict`, `empty`,
  `invalid_request`, `no_report`, `rate_limited`, `read_only`, `refund_overdue`, `renewed`,
  `stale_decision`, `stale_proposal`, `task_opened`), plus four UI fixtures the contract does not
  ship: `draft_pending`, `agent_error`, `contract_mismatch` and `refund_deadline_passed` (the
  service's clock past the refund deadlines: overdue flags in the queue, the overdue banner, late
  acts without a message; `src/api/adapters/fixtures.ts`). A 429 carries `Retry-After`;
  `read_only` refuses every write but the agent and the checkout evaluation.
- `?theme=light|dark`, `?present=1` (presenter mode). The persona is chosen under "Acting as".

## What the shell takes from the service

- **Read-only public demo.** `/readyz` `public_read_only` (PUBLIC_READ_ONLY of the API) gates every
  write: `useRoleGate` and `RoleGate` (`src/components/feedback`) disable each write control with
  "Read-only public demo" under it, never hide it; the agent and the checkout evaluation stay open.
  A 403 while read-only shows the read-only notice with the API's message (`ForbiddenRoleNotice`).
  `VITE_READ_ONLY=true` is only the fallback until `/readyz` answers.
- **The service's clock.** `/readyz` `clock`: the mode banner names its kind, whether it runs since
  the reset or is frozen at the snapshot (the read-only demo), and the snapshot instant; the top bar
  shows its 'now'. `useDemoClock` (`src/api/clock.ts`) advances a running clock between two polls
  and never a frozen or fixed one; every period that ends 'now' uses it, never the browser's date.
- **The cached label** is `/readyz` `label` as served ("Precomputed on AMD MI300X", or the rehearsal
  label), in the banner and on every cached text; never a fixed GPU name.
- **The demo cases** (`GET /api/demo/cases`) are loaded once by the shell for every role; the views
  take every golden id from them (`src/domain/demoCases.ts`). Before a reset the API answers 404 and
  every page shows "No demo reset yet" with the API's message: run `python -m apps.api.reset`.
- **The contract version.** The shell compares `/readyz` `contract_version` with `CONTRACT_VERSION`
  and shows a "Contract mismatch" line when the major or minor differs (a patch is compatible).

## Scripts

| Script | What it does |
| --- | --- |
| `npm run gen:types` | `src/api/types.gen.ts` from `contracts/json/api/*.schema.json` (json-schema-to-typescript) and `src/api/contract.gen.ts` (enums, endpoint roles, query defaults, demo users) |
| `npm run check:types-gen` | Fails when either generated file is stale |
| `npm run typecheck` | `tsc -b` over the app, the tests and the configs |
| `npm run lint` | ESLint with jsx-a11y, react-hooks and the import boundaries between view areas |
| `npm test` | Vitest + Testing Library (happy-dom) |
| `npm run test:e2e` | Playwright on a mock build (`build:mock` served by `vite preview`, so no test waits on on-demand transforms); `UI_E2E_SERVER=dev` uses the dev server, `UI_E2E_PORT` picks the port, `SCREENSHOT_DIR=<dir>` saves one screenshot per route |
| `npm run test:e2e:live` | Playwright against a running API (`playwright.live.config.ts`, `tests/e2e/live/`): the production build served by `vite preview` and proxied to `RETURNRING_API_URL`. Not in CI (no API there): see below |
| `npm run check:licenses` | Runtime tree MIT, BSD, Apache-2.0 or ISC; dev tree also PSF, PostgreSQL, MPL-2.0, CC-BY-4.0 |
| `npm run build` | Typecheck of the app and the configs, production build in live mode, then `scripts/check-bundle.mjs`: no mock marker, no example or mock module in the source maps, and (in a repo checkout) no string of `contracts/examples/api` in any file of `dist/`, `.map` files included |
| `npm run check` | All of the above except the e2e tests |

## Live end-to-end tests

`npm run test:e2e:live` runs three specs against the live API, with the ids of `GET /api/demo/cases`:

- `smoke.live.spec.ts`: every route (built from the demo cases) renders without console errors, with
  no number lacking provenance and no banned wording; the banner and the top bar follow `/readyz`.
- `walk.live.spec.ts`: the golden walk of `scripts/demo_walk.py` through the UI, in order: readiness,
  the demo cases, the queue, Giulia, Marco approved with his review date and the withdrawal clause,
  the ring cart evaluated and confirmed in the shop, Rossi, Elena and her catalogue task, the case
  with insufficient evidence, the dispute ("decision unchanged" when `changes_decision` is false),
  the three agent questions with the mock model, and the manager's pages. It writes: run it once
  after a fresh reset, against a writable API. It is skipped on the read-only demo.
- `readonly.live.spec.ts`: runs only when `/readyz` says `public_read_only`: every write control is
  disabled with "Read-only public demo", the agent and the checkout evaluation still answer, the clock
  is frozen at the snapshot.

The global setup fails first, with the fix, when the API does not answer, serves another
major.minor contract, or has no demo reset. `UI_E2E_RESET_CMD="<command>"` runs a reset first. From
the repo root, without Docker (SQLite file database, the serving snapshot of `make pipeline`):

```bash
export DATABASE_URL=sqlite:////tmp/returnring.db LLM_MODE=mock DEMO_MODE=live DEVICE=cpu \
       SERVING_DIR=serving DEMO_CLOCK=on PUBLIC_READ_ONLY=off
python -m apps.api.reset                                   # about a minute; decides every open return
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 &
cd apps/ui && RETURNRING_API_URL=http://127.0.0.1:8000 npm run test:e2e:live
# the read-only demo: a second API (here on the same store), then the suite again; the walk is skipped
PUBLIC_READ_ONLY=on uvicorn apps.api.main:app --host 127.0.0.1 --port 8001 &
RETURNRING_API_URL=http://127.0.0.1:8001 npm run test:e2e:live
```

`UI_E2E_PORT` picks the port of `vite preview` (5198 by default). The walk approves cases: reset again
before running it a second time.

A contract bump starts with `npm run gen:types && npm run typecheck`: components import
`src/api/models.ts`, never the generated files, so only adapters and models break.
`RETURNRING_CONTRACTS_DIR=<checkout>/contracts node scripts/gen-contract.mjs` tries another
contract checkout first. At runtime the shell compares `/readyz` `contract_version` with
`CONTRACT_VERSION` and shows a "Contract mismatch" line when the major or minor differs.

## Layout

```
src/api/        typed client (one function per route), queries, mutations, SSE, adapters, mock router
src/app/        shell, router (composes the views' index files), persona, display, hotkeys, SideNav
src/components/ provenance, mode, feedback, ui, decision, message (+ linked-accounts, economics, charts, records to come)
src/views/      operator, agent, manager, platform: one engineer each, no imports between them
                (only operator -> CaseAssistant from @/views/agent); navigation by URL
mock/           Vite middleware serving contracts/examples/api over HTTP
tests/e2e/      Playwright specs on the mock build; tests/e2e/live/ the specs against a live API
```

Rules that the code enforces: every number goes through `Figure` or `PlainNumber` inside a
`ProvenanceScope` (rule 11, US-906); the mode banner is always visible and never dismissible
(US-901); customer messages show connective text, template fields and the fixed legal clause as
separate layers (rule 10); `dangerouslySetInnerHTML` is banned; the wording of spec 16 is checked
in rendered text.

## Docker

`Dockerfile` builds the bundle with Node and serves `dist/` with nginx (SPA fallback, gzip). The API is
reached on the same origin through the Caddy proxy (`/api`, `/healthz`, `/readyz`). The build context
is `apps/ui` without `tests/` (`.dockerignore`): `npm run build` typechecks only the app and the
configs; the full `tsc -b` stays in `npm run typecheck`.
