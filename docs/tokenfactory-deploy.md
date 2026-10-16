# Deploy the live Token Factory demo on a CPU host

This is the current online demo path for ADR-18 and US-901. The API and UI run on a CPU host;
Gemma runs through the fixed AMD Token Factory endpoint. GPU training is a separate offline
job. `make tf-up` starts PostgreSQL, a one-shot seed/reset, the API, UI and Caddy. It starts no
GPU container and uses no historical MI300X draft cache.

## Prepare the host and snapshot

Use a Linux host with Docker Compose v2, GNU make, and enough memory for the serving snapshot.
The existing full-store recommendation is about 8 GB RAM for the stack; the API alone held
about 1.6 GB after warm-up, with peaks up to 2.2 GB in the CPU rehearsal on 14 October 2026
(one measured run outside Docker, not a container memory guarantee). No GPU or ROCm is needed.

Clone the release branch or commit being deployed. Copy the verified `serving/` snapshot to the
checkout root, including its `manifest.json`, tables, models and evaluation report. Running
services mount that snapshot read-only; do not copy or mount `data/truth/` into a service. A
checkout alone contains no snapshot. `make demo-data` can generate one on CPU, but this trains
new CPU models: do not describe those as the models from the AMD GPU session.

To deploy the trained GPU risk models, score the matching generated store with the saved
models, build any missing product anomalies, then export the snapshot with those artifacts:

```bash
python -m pipeline.anomalies --store data/gpu-synthetic --out data/gpu-synthetic
python -m ml.scoring --data data/gpu-synthetic \
  --models artifacts/amd-training/<risk-run>/risk/models --out data/gpu-synthetic
python -m pipeline.serving --data data/gpu-synthetic \
  --artifacts artifacts/amd-training/<risk-run> --out serving --seed 42
```

These are offline commands. They need the matching store and feature tables, not only the model
archive. Export fits the checkout model on CPU; risk-model training provenance remains in the
copied artifacts. The October 16 GPU session used synthetic data and trained GraphSAGE link
embeddings, not an ASOS return classifier. Do not replace a snapshot's model files in place:
scores, model versions and the manifest must agree.

## Configure the environment

```bash
cp .env.example .env
chmod 600 .env
```

Set these server-specific values in the ignored `.env` file:

| Variable | Value |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_DB` | `returnring` unless changing the database layout |
| `POSTGRES_PASSWORD` | A newly generated random password, replacing `change-me` |
| `DATABASE_URL` | `postgresql://returnring:<password>@postgres:5432/returnring`, matching the password above |
| `PUBLIC_HOST` | The public DNS name pointing to this host, without a URL scheme |
| `ADMIN_PASSWORD_HASH` | The bcrypt hash from `make admin-password`, enclosed in single quotes |
| `PUBLIC_READ_ONLY` | Leave unset or set `true` for the public demo |

Supply `RADEON_API_KEY` through the host's secret management or the environment of Compose. For
an interactive Bash session, read it without echoing or placing it in shell history:

```bash
read -r -s -p 'AMD Token Factory API key: ' RADEON_API_KEY
printf '\n'
export RADEON_API_KEY
```

The API requires this key. The reset receives an empty `RADEON_API_KEY` and uses `LLM_MODE=mock`,
so seeding hundreds of returns consumes no AMD quota. Never commit keys or paste a rendered
Compose configuration containing a real key into a log. Keep the key available when using the
`tf-*` commands: Compose validates the API environment even for logs and shutdown.

The overlay fixes the API to `DEVICE=cpu`, `LLM_MODE=tokenfactory`, `DEMO_MODE=live`,
`LLM_CONCURRENCY=1` and `VLLM_TIMEOUT_S=90`. `GEMMA_MODEL` is ignored by this adapter, which uses
the `Gemma-4-31B-IT` catalog ID. Run one API process/replica and no concurrent batch job with the
same key: pacing is per process while AMD's limit is shared by the team. Use synthetic inputs.

## Start and verify

For a rehearsal on the host's loopback interface:

```bash
make tf-up-local
```

Open `http://localhost:8080`. The API also listens on `127.0.0.1:8000` for local checks. The
local path needs no public hostname or admin hash.

For a public server, point `PUBLIC_HOST` at its IP and allow inbound TCP 80 and 443 in the cloud
firewall (and SSH only where needed for administration). Docker publishes no public API or
database port. Start the HTTPS entry point:

```bash
make tf-up
curl -fsS "https://<public-host>/healthz"
curl -fsS "https://<public-host>/readyz"
curl -s -o /dev/null -w '%{http_code}\n' "https://<public-host>/api/telemetry"
```

Readiness must report `status=ready`, `mode=live`, `llm_mode=tokenfactory`, `device=cpu` and
`public_read_only=true`, with database, scores, models and the AMD catalog probe all healthy.
Telemetry must return HTTP 401 to an anonymous request. Caddy obtains the HTTPS certificate;
check its logs if DNS or certificate validation is not ready yet.

Check the queue, the golden cases, checkout evaluation and one question to the case assistant in
the browser. The assistant makes a real AMD model call and retains the strict output validator.
Initial case drafts are deterministic mock seeds, explicitly labelled `mock`, even while the
runtime reports Token Factory. They are not claimed as Gemma generations or as MI300X-cached
outputs. Regeneration and approval are disabled in public read-only mode. Checkout evaluation
and the case assistant remain available and rate limited; they still record their audit data.

If a presentation needs regenerated Gemma drafts, do that deliberately before opening the
read-only demo and preserve their per-draft provenance. Changing `PUBLIC_READ_ONLY=false`
enables the existing write controls; on the public HTTPS path Caddy requires the `admin` basic
auth password for every API write. Do not use `make tf-up` as a way to retain edited demo state:
its one-shot reset recreates the disposable demo database and replaces drafts with mock seeds.

```bash
make tf-logs
make tf-down
```

Shutdown keeps PostgreSQL's named volume. Keep the service's host alive for a stable demo URL;
a notebook or a quick tunnel is a temporary rehearsal and has no uptime guarantee. Do not combine
this overlay with `compose.demo.yml` (cached local-vLLM provenance) or `compose.gpu.yml`.
