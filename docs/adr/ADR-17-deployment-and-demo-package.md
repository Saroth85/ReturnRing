# ADR-17: Deployment, the reset before the API, one proxy everywhere and the demo package

Status: accepted, 14 Oct 2026. Covers US-001, US-003, US-901, US-903, US-904 and US-905 on the
infrastructure side (spec sections 9, 11 and 17; CLAUDE.md rules 2, 5, 12 and 15). Changes
`infra/`, the `Makefile`, `.env.example`, `.github/workflows/ci.yml`, `scripts/demo_export.py`
and the README; no contract of `contracts/` changes.

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
   `PUBLIC_HOST` and turns basic auth on for `/system` (the admin page), `/api/telemetry` and
   every write (any method but GET, HEAD and OPTIONS under `/api/`) unless
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

## Consequences

- The API branch must read `PUBLIC_READ_ONLY` (true or false; on, off, 1, 0), `DEMO_CLOCK` (on,
  the default compose passes, or off), `LLM_CONCURRENCY` and `VLLM_TIMEOUT_S` (empty: the API's
  defaults), as ADR-15 section 15 of the API branch defines them; until it does,
  `tests/test_infra.py` lists them as pending (any other variable passed to the API and never
  read fails the test).
- The UI image must take `ARG VITE_READ_ONLY` before its build, so the public read-only build is
  the one compose asks for; `tests/test_infra.py` checks it once the phase 5 UI is in the tree.
- `make dev-up` wipes the decisions made since the last start: that is the demo's contract
  (US-903). To restart the API and keep the database, `docker compose ... up -d --no-deps api`
  skips the reset.
- The gpu-profile reset drafts every open return through vLLM, `LLM_CONCURRENCY` calls at once
  once the API branch's concurrent reset is merged (review H32), one at a time before; the export
  does not need it.
