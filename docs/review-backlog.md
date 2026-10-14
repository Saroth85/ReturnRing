# Open review findings (14 Oct 2026)

The last integration round (API contract 1.5.0, deployment, README) was reviewed from four
angles. These findings are open and are the starting list of the next session. Severity as the
reviewers ranked it; each item names where to look and a suggested fix.

The UI items are the planned migration of `apps/ui` from the pinned contract 1.1.0
(`apps/ui/contract-pin/`) to the API's contract 1.5.0.

## A judge following the README

- **[blocker] The UI the README describes is not in this commit: localhost:8080 shows a placeholder, and the UI CI job fails**  
  Where: apps/ui/Dockerfile, apps/ui/index.html; README.md (Quick start 'In the UI, pick who you are under Acting as...', Cached demo 'labelled Precomputed on AMD MI300X on every screen', Architecture row 'ui (React, Vite, TypeScript, nginx)', Licences 'npm run check:licenses', Development 'CI runs ... the UI checks'); .github/workflows/ci.yml job ui  
  Fix: Merge 5af3135 into this branch before the submission. In apps/ui/Dockerfile, add 'ARG VITE_READ_ONLY=false' and 'ENV VITE_READ_ONLY=$VITE_READ_ONLY' before 'RUN npm run build'. Then run the UI CI steps and the un-skipped tests in tests/test_ci.py and tests/test_infra.py. Until the merge lands, cut the README's UI claims (the Acting-as picker, the label 'on every screen', the UI licences and the UI CI).
- **[major] The 'Precomputed on AMD MI300X' label never checks where the cache came from; a CPU rehearsal package is labelled MI300X**  
  Where: apps/api/routes/health.py readiness() (label=CACHED_LABEL when demo_cache is ok); Makefile DEMO_GPU ?= AMD Instinct MI300X (AMD Developer Cloud), target demo-export  
  Fix: Remove the default from DEMO_GPU so demo-export requires it. Have demo-export refuse a cache whose entry models differ from GEMMA_MODEL unless ALLOW_EMPTY_CACHE or a REHEARSAL flag is set. In readiness(), show CACHED_LABEL only when the cache entries' model equals settings.gemma_model, or read generated_on from the package MANIFEST and show it.
- **[minor] demo-import, demo-verify, demo-local and demo-up need Python 3.11 or later; the README and Makefile say only 'host python3'**  
  Where: scripts/demo_export.py:36 'from datetime import UTC, datetime'; Makefile demo-import, demo-verify (python3 -m scripts.demo_export)  
  Fix: Use 'from datetime import datetime, timezone' with UTC = timezone.utc (works on 3.8+), or state Python 3.11 or later in the README's cached-demo section.
- **[minor] A judge cannot get the cached-demo package the README tells them to import on a laptop**  
  Where: README.md 'The cached demo' ('On the demo server, or on a laptop: make demo-import'); .gitignore demo-cache/, /demo-cache.tar.gz  
  Fix: Publish demo-cache.tar.gz as a GitHub release asset and add 'curl -LO <url>' with its sha256 before 'make demo-import', or say that the laptop path is for the team only.
- **[minor] The compose PUBLIC_HOST guard can never fire because .env.example sets PUBLIC_HOST=localhost**  
  Where: .env.example 'PUBLIC_HOST=localhost'; infra/compose.public.yml 'SITE_ADDRESS: ${PUBLIC_HOST:?set PUBLIC_HOST in .env ...}'  
  Fix: Leave 'PUBLIC_HOST=' empty in .env.example, with the sslip.io example in the comment, so the :? guard stops a public start that has no host.
- **[minor] The README's image-licence claim 'pulled unmodified by digest' is not true for the API and UI base images**  
  Where: apps/api/Dockerfile 'FROM python:3.12-slim'; apps/ui/Dockerfile 'FROM nginx:1.27-alpine' (the UI branch adds node:22.22.0-bookworm-slim); README.md Licences  
  Fix: Pin python, nginx and node by tag@sha256, as postgres and caddy already are, and add the Python base image (PSF and Debian packages) to the README's image list. Or change the wording to 'pinned by digest or version'.
- **[minor] The README's command table leaves out the steps the demo walk and the cached stack need**  
  Where: README.md command table ('make reset', 'make demo-walk', 'make dev-down'); Makefile demo-reset, demo-down  
  Fix: In the table, say 'run make reset (dev) or make demo-reset (cached) before make demo-walk', and list make demo-reset and make demo-down beside make demo-local.
- **[minor] The API memory peak quoted in the README did not reproduce**  
  Where: README.md Quick start ('1.57 GB after the warm-up and at most 1.81 GB during the demo walk'); infra/docker-compose.yml api comment  
  Fix: Re-measure inside the container (docker stats) and quote the range seen, for example 'about 1.6-2.2 GB', with the date.
- **[minor] First-start race: postgres can be marked healthy while its init-only server is up, and the reset has no retry**  
  Where: infra/docker-compose.yml postgres healthcheck 'pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}'; apps/api/reset.py main() (connects at once, no retry)  
  Fix: Use 'pg_isready -h 127.0.0.1 -p 5432 -U ... -d ...' in the healthcheck (the temporary server has no TCP listener), or retry the first database connection in apps/api/reset.py for about 30 s.

## Security of the public deployment

- **[major] No restart policy on the long-running public services: one crash or reboot takes the public demo down until someone logs in**  
  Where: infra/docker-compose.yml: services postgres (~line 53), api (~88), ui (~132), proxy (~144); compose.demo.yml and compose.public.yml add none  
  Fix: Add `restart: unless-stopped` to postgres, api, ui and proxy in docker-compose.yml. Keep `reset` at `"no"` so a daemon restart keeps the database rather than resetting it; pgdata persists. On a daemon restart the API reconnects lazily, so depends_on ordering is not needed.
- **[minor] Agent limit is keyed only by the named person, so one anonymous client can lock the agent for every visitor on a public read-only live demo**  
  Where: apps/api/routes/other.py:163-165 (agent_ask: authorize, then agent.check_rate(request.app, body.asked_by), then check_public_rate); apps/api/services/other/agent.py:693-700  
  Fix: On the public read-only API, call check_public_rate before agent.check_rate. Also key the person limiter by (client_key(request), asked_by) when public_read_only(request), or skip it there, so one client cannot spend another client's budget. Add a test with two X-Forwarded-For values.
- **[minor] Basic auth on /system does not protect the admin page in a single-page UI, and it prompts judges for a password on a reload of the benchmarks page**  
  Where: infra/Caddyfile:17 (`@admin path /system /system/* /api/telemetry /api/telemetry/*`)  
  Fix: Remove /system and /system/* from @admin and protect only the data endpoints (/api/telemetry). If an admin page needs protection, protect the API paths its data comes from, not its client route. Update ADR-15 section 15 and ADR-17 to match.
- **[minor] Every wrong basic-auth attempt costs a bcrypt-14 comparison: an unauthenticated CPU drain on the small public droplet**  
  Where: infra/Caddyfile:18-20 and 27-29 (basic_auth), Makefile:129-130 (`caddy hash-password`, cost 14), .env.example ADMIN_PASSWORD_HASH comment ('$2a$14$...')  
  Fix: Generate the hash at a lower cost, for example bcrypt cost 10 (about 15 ms), in `make admin-password` and the .env.example note. Alternatively, rate-limit the @admin paths at the proxy.
- **[minor] Public rate-limiter keys are never evicted**  
  Where: apps/api/ratelimit.py:33-43 (RateLimiter._calls)  
  Fix: In retry_after, delete the key when its deque is empty after pruning, or periodically sweep old keys. Key IPv6 clients by their /64 prefix.
- **[minor] The request path goes into the access log unescaped, so an encoded newline forges log lines**  
  Where: apps/api/middleware.py:124-131 (ACCESS.info(... path=%s ..., scope.get("path")))  
  Fix: Log a safe form of the path: `scope.get("raw_path", b"").decode("latin-1")`, or `path.encode("unicode_escape").decode()`, or `%r`.
- **[minor] The public /readyz echoes a malformed VLLM_BASE_URL in full, including any credentials or query**  
  Where: llm/client.py:119-120 (check_local_url: f"VLLM_BASE_URL must be a plain http(s) URL, got {base_url!r}"); apps/api/routes/health.py:49-52 (probe_vllm returns str(exc) as the public check detail); apps/api/services/generation.py:249 and other.py agent 503 message  
  Fix: Do not echo the URL: say which part is refused (for example `VLLM_BASE_URL has a user or a query: refused`), or redact userinfo and query before formatting.
- **[minor] Unauthenticated GET /api/portfolio/matrix blocks the single worker's event loop for seconds**  
  Where: apps/api/routes/manager.py:89-110 (portfolio_matrix, then large_answer: json.dumps and gzip.compress in the event loop)  
  Fix: Run the serialization and compression with asyncio.to_thread. Better, cache the encoded and gzipped bytes per (reset, clock day), since the snapshot is immutable and only decisions change.

## UI readiness (migration to contract 1.5.0)

- **[blocker] [UI migration] The UI's write bodies are 1.1.0-shaped, and the live API refuses them with 422**  
  Where: UI branch worktree-wf_ee60d6ed-5bf-5 @5af3135: apps/ui/src/views/operator/state/requests.ts:13,24,42-43,66,100; src/api/mutations.ts:46; src/views/operator/case/sections/ExplanationSection.tsx:240-244  
  Fix: Build every body like the 1.5.0 examples: message {lang, sentence_ids}; message null for lift and renew; override with sentence_ids; regenerate {decision_id, languages, requested_by}; read regenerate.decision.decision_id. Add a renew action next to lift (audit action restriction_renewed is human-only with no reason). Derive `Lever` in models.ts as `DecisionView['levers'][number]`.
- **[blocker] [UI migration] Generated types and contract constants fail against 1.5.0, and read paths crash on the new shapes**  
  Where: UI @5af3135: scripts/gen-contract.mjs:198 (DRAFT_TONES), scripts/gen-types.mjs:76, src/api/models.ts:52,91, src/components/message/*, src/api/adapters/messageSegments.ts, src/views/operator/case/{sections/ExplanationSection.tsx,rail/ActionBar.tsx:52,sheets/DecisionSheets.tsx:51,CasePage.tsx:230-235}, src/views/operator/{outbox/OutboxPage.tsx:129,record/DecisionRecordPage.tsx:62}, src/components/re  
  Fix: Remove DRAFT_TONES from gen-contract. Set `ignoreMinAndMaxItems: true` in gen-types. Regenerate and keep models.ts as the only adapter layer. Rebuild the rule-10 segmentation from texts[].text, fields and the clause: split once at the first blank line for the clause, and locate each non-null field value exactly once in the body, else show the text as unverified. Render statements from facts[]. Label restriction_renewed. Handle a null outbox_message and the {it, en} customer texts.
- **[blocker] [UI migration] The golden-case ids are hard-coded, the live ids differ, and GET /api/demo/cases has no client function**  
  Where: UI @5af3135: src/views/platform/constants.ts:27-34; src/views/platform/copy.ts:101-131 (beats); src/views/platform/shop/presets.ts:3-16; src/views/manager/constants.ts:19-31; src/views/operator/index.ts:23; src/views/operator/compare/ComparePage.tsx:203; src/views/agent/copy.ts:53; src/api/client.ts (route table R)  
  Fix: Add `api.demoCases()`, load it once in the shell (all roles), and derive from it: beats, compare a/b, the preflight ids (marco.decision_id, rossi_card.decision_id), shop presets (carts[].request sent as is), matrix highlights (customer_id and cluster_id) and the dispute link. Show a 'run the reset' state on 404. Build the agent's suggested link question from the Marco card's linked_accounts.links: C-MARCO-1~C-MARCO-3 still exists live, but the case customer is C-MARCO-7.
- **[major] [API] demo_cases.schema.json defines CartLine and CheckoutEvaluateRequest differently from the request schemas, so the UI type generation fails**  
  Where: apps/api/schemas.py:202-206 (ApiModel json_schema_serialization_defaults_required=True), 3556-3565 (CartLine, CheckoutEvaluateRequest), 4677-4681 (DemoCart.request); scripts/export_api_schemas.py:48-51; contracts/json/api/demo_cases.schema.json  
  Fix: Give CartLine and CheckoutEvaluateRequest `model_config = ConfigDict(extra='forbid', json_schema_serialization_defaults_required=False)` so both modes export the same schema (the response always sends quantity anyway). Re-export, and add a test that every $def name has an identical body in every contracts/json/api/*.schema.json.
- **[major] [UI migration] The mock router does not serve the 1.5.0 example table: .sse streams, fixture scenarios that hide contract examples, and a role check that pre-empts them**  
  Where: UI @5af3135: src/api/adapters/mockRouter.ts:268-297,380; src/api/adapters/fixtures.ts:5-11; src/api/adapters/mock.ts:9 (glob of *.json only)  
  Fix: For a text/event-stream route, load the .json sibling as the event list. Drop the UI fixtures that the contract now covers and keep only draft_pending, agent_error and contract_mismatch. Before the fixture role check, look for a contract route whose request body matches, and add approver to the decision BODY_KEYS.
- **[major] [UI migration] Public read-only demo: the bundle never learns it is read-only, so its write buttons only produce 403s**  
  Where: UI @5af3135: apps/ui/Dockerfile (no ARG VITE_READ_ONLY); src/components/feedback/roles.ts:14,20; shop confirm (src/api/mutations.ts:82, ShopPage); src/views/agent (cached-mode comment); src/api/client.ts:100. Branch 5156e3a: infra/docker-compose.yml:128-130, infra/compose.demo.yml:31-34  
  Fix: Read readyz.public_read_only at runtime and gate every write on it, except agent ask and checkout evaluate. Keep `ARG VITE_READ_ONLY=false` in the Dockerfile only as a fallback. Treat a 403 while public_read_only is true as the read-only notice. Gate the shop confirm. In cached mode show the agent as unavailable. Change the 401 text to /system.
- **[major] [UI migration] The demo clock is ignored: the UI never shows readyz.clock and dates some Metrics with the browser clock**  
  Where: UI @5af3135: src/components/mode/useModeView.ts, ModeBanner; src/views/platform/shop/ShopPage.tsx:73; src/views/platform/demo/DemoPage.tsx:31; src/views/platform/demo/preflight.ts:62  
  Fix: Show kind, now and snapshot from readyz.clock in the mode banner, advancing it with (Date.now() - fetchedAt) and re-reading readyz. Take Metric periods from that clock. Mark telemetry times as wall time. In preflight, accept vllm 'disabled' when readyz.llm_mode is mock.
- **[major] [Examples vs live] The Giulia checkout example shows a specific size tip and size-dependent probabilities that the live service never produces**  
  Where: scripts/build_api_examples.py:2963 (SizeAdvice.of('specific','M')); apps/api/services/other/checkout.py:168-175; ml/reference/checkout.py:29-50  
  Fix: Build checkout_evaluate_response.giulia through the live size_advice and model (measurement guide, equal p for both sizes), or remove the 'specific' kind from the example set. Make the UI shop beat show the measurement-guide text and drop 'Switch to M'.
- **[major] [UI migration] Error statuses and codes the UI must handle against 1.5.0, including a photo limit above the contract**  
  Where: UI @5af3135: src/views/operator/case/sheets/*, src/views/platform/shop/*, src/views/manager/kpi, src/views/platform/constants.ts:11 (PHOTO_MAX_MB=8), src/views/platform/inspection/inspection.ts:64  
  Fix: Map each status above to its notice and recovery: refetch on stale_proposal and stale_decision, re-evaluate on checkout 409, empty states on 404 no_report and no_demo_reset. Set the photo limit to floor(7,000,000 × 3 / 4) bytes, read from the schema's maxLength.
- **[minor] [API] 429 rate_limited carries no Retry-After header**  
  Where: apps/api/roles.py:127 (check_public_rate); apps/api/errors.py:91  
  Fix: Have the ApiError for 429 set `Retry-After: <ceil(wait)>`. Pass the wait from limiter.retry_after() into rate_limited() and add the header in the error handler.
- **[minor] [API] The catalogue-task check is weaker than ADR-15 H14, and the live Elena card's anomaly does not contain her return**  
  Where: apps/api/services/manager/catalog.py:260-272 (open_task); apps/api/services/cases.py:820-846 (card anomaly)  
  Fix: Decide the semantics. Either enforce H14 (404 unless the return is in the anomaly's queued ids), or keep it accepting and document that any queued return of the variant may be named, adding the case's own return to the card's queued_return_ids. The second keeps the Elena beat working.
- **[minor] [Examples vs live] The inspection examples contradict the live mock vision for their own photo**  
  Where: scripts/build_api_examples.py:3202,3669; apps/api/services/other/inspections.py:229-236 (mock_outcome), 704-712 (no_proposal)  
  Fix: Build the inspection examples through MockVision. Pick photo bytes with sha256[0] % 3 == 1 (damaged) for the awaiting case, and 2 or unreadable for manual review. Document that the claim beat needs LLM_MODE=mock or vllm.
- **[minor] [Examples vs live] The demo's evidence and dispute beats change nothing live; model_versions keys and the Marco checkout state differ from the examples**  
  Where: apps/api/services/demo.py:106-140 (find_dispute); scripts/build_api_examples.py MODEL_VERSIONS (around 813, 4288); checkout_evaluate_response.marco example  
  Fix: In find_dispute, prefer a link whose recompute changes quadrant or levers (dry-run the candidates), or have the UI say 'decision unchanged'. Generate the example MODEL_VERSIONS from the serving manifest's key set. Order the live pitch so Marco is approved before the shop beat.
- **[minor] [CI] The ui job cannot pass on this branch: apps/ui is still the placeholder**  
  Where: .github/workflows/ci.yml:33-55; apps/ui (Dockerfile and index.html only at 5156e3a)  
  Fix: Merge the migrated UI before the deadline. Until then, guard the job with `if: hashFiles('apps/ui/package-lock.json') != ''`.
