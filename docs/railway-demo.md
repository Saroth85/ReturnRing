# Public demo on Railway

Running instance: **https://returnring-demo-production.up.railway.app/demo**.
First deployed on 17 October 2026 (Europe/Rome), from code revision
`5793a80b82407000b39aa2586d0942b10e0a2210`, project `ReturnRing`, service `returnring-demo`.
The service is pinned to that revision; later documentation commits do not change it.

Configure one CPU service with Dockerfile path `infra/docker/public-demo.Dockerfile`:
the existing API and built UI share one HTTPS origin.
Gemma runs through AMD Token Factory; no GPU service, separate database service or volume is
needed for this disposable, read-only event demo. The service uses Railway's `PORT`.

## Runtime variables

Set these on the service before attaching its repository source or committing staged changes:

| Name | Value |
| --- | --- |
| `RADEON_API_KEY` | The team's AMD key, as a server secret; never a build argument or UI variable. |
| `DEMO_PACKAGE_URL` | HTTPS URL of the synthetic snapshot ZIP. |
| `DEMO_PACKAGE_SHA256` | SHA-256 of that exact ZIP; changing the asset requires changing this value. |
| `DEMO_ADMIN_PASSWORD` | A separate random secret for the telemetry route, Basic auth user `admin`; missing means telemetry stays inaccessible. |
| `PUBLIC_READ_ONLY` | `true` (default). |
| `LLM_CONCURRENCY` | `1` (default). |
| `VLLM_TIMEOUT_S` | `90` (default). |
| `TRUSTED_PROXY_HOPS` | `1` (default): the platform's edge writes the last `X-Forwarded-For` entry, the one the rate limits key a client on; `0` only if uvicorn is reached directly (ADR-18, amendment). |

The image defaults to `DEVICE=cpu`, `LLM_MODE=tokenfactory`, `DEMO_MODE=live`. Runtime downloads
are bounded, hash checked and checked against the package file manifest before extraction.
The serving snapshot is then checked by the API against its own existing manifest.
No ground-truth table or API key belongs in this archive.

The initial small package has 1,000 synthetic customers, seed 7, scale 0.05, and an
**illustrative calibration**. Its CPU models and synthetic quality figures are separate from
the full-store MI300X training session in [the session report](amd-training-session-2026-10-16.md).
Three saved drafts were validated through the real Gemma HTTP route; other initial drafts
retain their mock provenance. The live read-only investigator uses Token Factory.
These distinctions must remain visible in the demo and submission.

The SQLite database is restored into temporary runtime storage on every start. Judge sessions,
agent logs and any administrative changes disappear on restart or redeploy. Do not use this
deployment for merchant data or as a persistent production service.

## Prepare the archive

With an existing hash-verified synthetic serving snapshot and a matching demo database:

```powershell
.venv\Scripts\python.exe -m scripts.public_demo_bootstrap pack `
  --serving <snapshot-directory> --database <matching-demo.sqlite> `
  --out artifacts/public-demo/returnring-demo-snapshot.zip
```

Publish that ZIP as a versioned project release asset and use its exact download URL and
printed SHA-256. All tables must be labelled synthetic. The packager uses SQLite's backup API
and copies only files listed by the serving manifest; it verifies their original hashes.
`DEMO_PACKAGE_FILE` is a local-only alternative for testing without a remote download.

## Deploy and verify

Use the reviewed branch and a pinned commit when first connecting the service source. Stage
its settings and secret variables before triggering the first deployment. Keep one replica;
set the healthcheck to `/healthz` with a 120-second startup allowance, and restart on failure
at most three times. Configure these service settings through Railway's API or dashboard;
the deprecated `railway.json` format is not used.
Start with a 1 GB memory and 1 vCPU cap for the small fixture, then inspect measured memory.
The full 20,000-customer snapshot needs more memory and must not be substituted silently.
Configure the workspace compute hard limit within the owner's authorised budget, and inspect
the subscription fee separately. Do not remove the hard limit.

After a successful build and generated Railway domain, check from outside Railway:

- `/healthz` returns 200 and `/readyz` says `live`, `tokenfactory`, `cpu`, contract `1.9.0`,
  `public_read_only=true`, and the shared-provider readiness detail.
- The Demo page, golden case cards, queue and direct page links work.
- Telemetry without admin credentials returns 401; a write such as regeneration returns 403.
- A read-only investigator request streams validated output from AMD; no model key appears in
  the browser bundle or response.

Source: [Railway cost controls](https://docs.railway.com/pricing/cost-control),
[Railway Docker deployment](https://docs.railway.com/guides/dockerfiles).

## Verified first deployment

- Railway build and startup succeeded in `europe-west4`, one instance capped at 1 GB and 1 vCPU.
- External `/readyz` was ready, live, Token Factory, CPU, contract 1.9.0, read-only.
- All three saved Gemma golden-case drafts loaded and passed the existing case-card contract.
- Public telemetry returned 401, regeneration 403, missing API/asset routes 404.
- The real investigator returned HTTP 200 with an SSE answer, two read-tool steps, one Gemma
  model call, no validator codes and `generation_path=model`. End-to-end time was 9.724 seconds
  for this one question, not a latency benchmark.
- The public Demo page passed its preflight in a browser; golden case cards and matrix took
  359 ms in that browser observation, not a load-test result.
- Railway reported about 0.308 GB memory after this check. The owner authorised a maximum
  hosting budget of USD 20 and reported the workspace unblocked before provisioning.

Archive: [versioned synthetic demo snapshot](https://github.com/Saroth85/ReturnRing/releases/tag/demo-snapshot-2026-10-17),
SHA-256 `27ac15b448d59cf298bd75dee1b2674631347063ca1115b804f8b2ab32466373`.
