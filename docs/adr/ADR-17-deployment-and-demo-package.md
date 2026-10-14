# ADR-17: Deployment, the reset before the API, one proxy everywhere and the demo package

Status: accepted, 14 Oct 2026; amended the same day after the review of the public deployment
(section "Amendments after the review"). Covers US-001, US-003, US-901, US-903, US-904 and
US-905 on the infrastructure side (spec sections 9, 11 and 17; CLAUDE.md rules 2, 5, 11, 12, 13
and 15). Changes `infra/`, the `Makefile`, `.env.example`, `.github/workflows/ci.yml`,
`scripts/demo_export.py` and the README; the amendments change the API contract to 1.5.1
(ADR-15 section 16).

## Context

ADR-16 left the infrastructure open: the API needs the serving snapshot mounted and a database
filled by the reset, the pipeline job only ran the generator, nothing ran the reset or the
telemetry sampler, the API and the telemetry mounted the whole `artifacts/` folder (review F2), the
public proxy had no authentication (review H21) and `make demo-export` was an echo. A judge must
start the product in at most three commands on a laptop without a GPU (US-904), and the public
demo must work with the GPU off (US-901).

## Decision

1. **The judge's path.** `cp .env.example .env`, `make demo-data`, `make dev-up`. `demo-data`
   runs the whole chain of `make pipeline` (store, graph, features, anomalies, risk models and
   scores, serving snapshot) in the `pipeline` job container, profile `jobs`, so the host needs
   Docker and make only. `tests/test_infra.py` checks that the job's commands are the Makefile
   recipes, in order, with `--no-results` for the risk step (a container never rewrites
   `docs/results`). `dev-up` runs `demo-data` itself when `serving/` is missing.
2. **The reset runs before the API, every time.** A one-shot `reset` service (the API image,
   `python -m apps.api.reset`, the API's own environment, the snapshot read-only) runs after
   PostgreSQL is healthy; the API depends on it with `service_completed_successfully`. Every
   `make dev-up` therefore starts from the golden state, and a failed reset stops the stack
   instead of leaving an empty queue. `make reset`, `gpu-reset` and `demo-reset` run it again on
   a running stack. In the gpu profile it drafts through vLLM and waits for vLLM to be healthy.
3. **The API mounts only the snapshot and the benchmark results**, both read-only, with
   `SERVING_DIR`, `BENCHMARKS_DIR`, `GEMMA_MODEL`, `PUBLIC_READ_ONLY` and `DEMO_CLOCK` in its
   environment (with `LLM_CONCURRENCY` and `VLLM_TIMEOUT_S`). Its healthcheck reads the body of `/readyz` (which answers 200 in every state):
   healthy when the status is not `not_ready` and the scores and models are loaded; `degraded`
   (vLLM down, no demo cache) still serves templates. Start period 90 s; one uvicorn worker.
4. **No running service mounts `data/` or `artifacts/`** (rule 12, review F2): the amd-smi writer
   and the sampler share a named volume; the sampler stores each reading in `telemetry_samples`.
   The strict xfail of F2 in `tests/test_infra.py` is now a passing test over a declared list of
   running services; a new service must be classified as running or offline.
5. **One proxy in every profile.** Caddy serves the UI and `/api`, `/healthz`, `/readyz` on one
   origin (the UI's nginx serves only the bundle). On a laptop it listens on `127.0.0.1:8080`, in
   plain HTTP, with no authentication. On a server reachable from the Internet
   (`infra/compose.public.yml`, used by `make gpu-up` and `make demo-up`) it serves HTTPS on
   `PUBLIC_HOST` and turns basic auth on for `/api/telemetry` (the admin page's data; since the
   amendments, not its client route `/system`) and every write (any method but GET, HEAD and
   OPTIONS under `/api/`) unless
   `PUBLIC_READ_ONLY=true`, in which case the writes reach the API, which refuses them. The
   password hash is required: Compose refuses to start a public proxy without it. Request bodies
   over 10 MB stop at the proxy (review H24); no response compression, so the agent's event
   stream is never buffered. The Caddyfile was run with Caddy 2.11.7 in each mode: anonymous
   writes and telemetry get 401 on a public server, reads pass.
6. **The cached demo package.** `make batch-generate` (gpu, profile `gpu-jobs`, never started by
   `up`) fills `serving/llm_cache/`; `make demo-export` packs `demo-cache/`: the snapshot
   (checked against its own manifest first) with the cache, the benchmark pointers and the run
   files they name, and `MANIFEST.json` with the sha256 and size of every file, the snapshot
   instant and seed, the models, generation paths and fallback reasons of the cache, the hardware
   it ran on and the nature of each part (rule 11). An empty cache is refused unless the export
   is a declared CPU rehearsal. `make demo-up` and `make demo-local` verify every hash before
   starting; `compose.demo.yml` mounts the package at the API's paths, so the checkout's own
   `serving/` and `data/` are never read. The script uses the standard library only: the droplet
   host and the demo server have no project Python. No image is part of the package (ADR-12).
7. **Pinned images.** postgres and caddy are pinned by tag and multi-arch digest like the GPU
   images; `make admin-password` hashes with the proxy's own image.
8. **CI.** A UI job on Node 22 (`npm ci`, lint, typecheck, unit tests, generated types, licences,
   build), the Python job with the generated-contract checks, the optional GraphSAGE job with the
   CPU build of PyTorch, and gitleaks over the whole history.

## Verification without Docker (14 Oct 2026)

No Docker daemon was available where this was built, so each service's command ran on the host
with the environment `docker compose config` renders for it (`docker compose` 5.6.0 renders every
profile in the tests): the pipeline job's command in 9 min 39 s on 4 shared vCPUs (exit 0), the
reset on PostgreSQL 16 (623 open returns, none failed, 42 s), the API healthy 38 s after its
start by the compose healthcheck code, and the demo walk with every check passed. The batch ran
against the fake vLLM of `bench/fake_vllm.py` (623 entries), the package was built and verified
with the system `python3` (no project package installed), carried through `tar`, and the cached
reset passed on it with 623 drafts served from the cache; the walk passed every step but the case
assistant, which answers the 503 documented for cached mode (the walk of the API branch expects
it, review H35). Nature: real timings of runs on the synthetic store.

## Verification after the merge with the API branch (14 Oct 2026)

The same host procedure on the merged tree (contract 1.5.0, settings read under the names compose
passes): the pipeline job's command rebuilt the store and the snapshot in 8 min 19 s (exit 0;
only `features/evidence_quality` differs from the snapshot of the API branch, by the
missing-evidence sentences of US-209); the reset decided the 623 open returns with their drafts,
none failed, in 43 s, also with `PUBLIC_READ_ONLY=true` (the reset writes the database directly);
the API was healthy by the compose healthcheck about 30 s after its start. `make demo-walk`
passed its twelve steps in each variant: the dev profile (first queue page 0.578 s, checkout
evaluation 0.092 s); the same API with `PUBLIC_READ_ONLY=true`, twice without a reset (the seven
writes of the pitch refused with 403 `forbidden_role`, the evaluation and the agent answering);
the cached package of a fake-vLLM batch (623 entries, `make demo-export`, `demo-import`,
`demo-verify`), read-only by default (the agent's 503 `llm_unavailable`) and with
`PUBLIC_READ_ONLY=false`. Caddy 2.11.7 run with this Caddyfile in front of each API in public
mode (`ADMIN_AUTH=on`): anonymous reads 200; `/system` and `/api/telemetry` 401, 200 with the
admin's credentials; an anonymous approval reaches the API and gets 403 when read-only, 401 at the
proxy otherwise. Nature: real timings of runs on the synthetic store; the cached drafts of the
rehearsal come from the fake server, not from Gemma.

## Amendments after the review (14 Oct 2026)

The review of the deployment, the README and the public demo (docs/review-backlog.md) changed:

- **The MI300X label needs the package to say so** (rules 11 and 13; ADR-15 section 16).
  `DEMO_GPU` has no default: `make demo-export DEMO_GPU="AMD Instinct MI300X (AMD Developer
  Cloud)"` names the hardware, which must name the MI300X, and every draft must be
  `GEMMA_MODEL`'s (read from `.env`). `REHEARSAL=1` (a fake vLLM server, another model) or
  `ALLOW_EMPTY_CACHE=1` packs a rehearsal, recorded as one in `MANIFEST.json`
  (`llm_cache.rehearsal`, `gemma_model`). `compose.demo.yml` mounts the package's
  `MANIFEST.json` read-only at `/app/MANIFEST.json`, next to the snapshot as in `demo-cache/`,
  and the API shows the MI300X label only when it says so and the cache entries are
  `GEMMA_MODEL`'s; a rehearsal label otherwise. `scripts/demo_export.py` runs on Python 3.8 or
  later (`timezone.utc`, not `datetime.UTC`; a test parses it with the 3.8 grammar).
- **Restart policies.** postgres, api, ui and proxy restart `unless-stopped`, like the telemetry
  sampler: a crash or a reboot of the public server no longer takes the demo down until someone
  logs in. The reset stays `"no"`, so a daemon restart keeps the database (`pgdata`); the API
  reconnects lazily.
- **First start.** The postgres healthcheck runs `pg_isready -h 127.0.0.1 -p 5432`: the image's
  init-only server listens on the Unix socket only, so the database is healthy once the real
  server is up. The reset also retries its first connection for about 30 s.
- **Basic auth on the data, not on the client route.** The single-page UI serves `/system`
  from any path, so basic auth there protected nothing and asked judges for a password on a
  reload of the benchmarks page. `@admin` is `/api/telemetry` and `/api/telemetry/*`: the admin
  page's data (spec 11, "the admin page and the telemetry are protected", met through the data
  the page shows; readiness and benchmarks are public views anyway). `make admin-password`
  hashes at bcrypt cost 10 (`caddy hash-password --algorithm bcrypt --bcrypt-cost 10`, about
  15 ms a check) instead of Caddy's default 14: every wrong attempt costs one comparison on the
  small public droplet.
- **`PUBLIC_HOST` is empty in `.env.example`**, with the sslip.io example in the comment, so the
  `:?` guard of `compose.public.yml` stops a public start that names no host. The laptop
  profiles (`make dev-up`, `make demo-local`) and the tests need no host.
- **The public API** (ADR-15 section 16): the public rate limit before the agent's, the agent's
  limit per client and person, keys forgotten when idle, IPv6 clients by their /64, `Retry-After`
  on a 429, no `VLLM_BASE_URL` echoed, the access log path percent-encoded, the matrix encoded
  off the event loop.
- **The README** quotes the API's memory as measured once (about 1.6 GB after the warm-up; the
  peak during the demo walk varied between runs, up to about 2.2 GB; on the host, not in a
  container; not re-measured, no Docker daemon was available), says which images are pinned by
  digest (the GPU images, PostgreSQL, Caddy) and which by version tag (the Python, Node and nginx
  bases), lists the Python base image, names `make demo-reset` and `make demo-down`, and says
  that the demo package is published as a GitHub release asset after the GPU session; until then
  the laptop path is for the team.

The verification sections above are kept as they ran: their `/system` 401 is the behaviour
before these amendments.

## Consequences

- The API reads every variable compose passes it under the same name (`apps.api.settings`:
  `PUBLIC_READ_ONLY` true or false, on, off, 1, 0; `DEMO_CLOCK` on, the default compose passes,
  or off; `LLM_CONCURRENCY` and `VLLM_TIMEOUT_S`, empty for the API's defaults, 32 and 20 s;
  `BENCHMARKS_DIR` in the benchmarks service), as ADR-15 section 15 defines them. Since the
  merge with the API branch (contract 1.5.0) `tests/test_infra.py` lists no pending setting: a
  variable passed to the API and never read fails it, and each profile's merged environment,
  interpolated against `.env.example`, must give the expected settings (the demo profile
  read-only).
- The UI image must take `ARG VITE_READ_ONLY` before its build, so the public read-only build is
  the one compose asks for; `tests/test_infra.py` checks it once the phase 5 UI is in the tree.
- `make dev-up` wipes the decisions made since the last start: that is the demo's contract
  (US-903). To restart the API and keep the database, `docker compose ... up -d --no-deps api`
  skips the reset.
- The gpu-profile reset drafts every open return through vLLM, `LLM_CONCURRENCY` calls at once
  (review H32); the export does not need it.
