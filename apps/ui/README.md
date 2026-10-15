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
  `stale_decision`, `stale_proposal`, `task_opened`), plus three UI fixtures the contract does not
  ship: `draft_pending`, `agent_error`, `contract_mismatch` (`src/api/adapters/fixtures.ts`). A 429
  carries `Retry-After`; `read_only` refuses every write but the agent and the checkout evaluation.
- `?theme=light|dark`, `?present=1` (presenter mode). The persona is chosen under "Acting as".
- The public read-only demo follows `/readyz` `public_read_only`; `VITE_READ_ONLY=true` is the
  fallback until `/readyz` answers.

## Scripts

| Script | What it does |
| --- | --- |
| `npm run gen:types` | `src/api/types.gen.ts` from `contracts/json/api/*.schema.json` (json-schema-to-typescript) and `src/api/contract.gen.ts` (enums, endpoint roles, query defaults, demo users) |
| `npm run check:types-gen` | Fails when either generated file is stale |
| `npm run typecheck` | `tsc -b` over the app, the tests and the configs |
| `npm run lint` | ESLint with jsx-a11y, react-hooks and the import boundaries between view areas |
| `npm test` | Vitest + Testing Library (happy-dom) |
| `npm run test:e2e` | Playwright on a mock build (`build:mock` served by `vite preview`, so no test waits on on-demand transforms); `UI_E2E_SERVER=dev` uses the dev server, `UI_E2E_PORT` picks the port, `SCREENSHOT_DIR=<dir>` saves one screenshot per route |
| `npm run check:licenses` | Runtime tree MIT, BSD, Apache-2.0 or ISC; dev tree also PSF, PostgreSQL, MPL-2.0, CC-BY-4.0 |
| `npm run build` | Typecheck of the app and the configs, production build in live mode, then `scripts/check-bundle.mjs`: no mock marker, no example or mock module in the source maps, and (in a repo checkout) no string of `contracts/examples/api` in any file of `dist/`, `.map` files included |
| `npm run check` | All of the above except the e2e tests |

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
tests/e2e/      Playwright specs
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
