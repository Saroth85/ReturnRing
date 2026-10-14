# Open review findings (14 Oct 2026)

The last integration round (API contract 1.5.0, deployment, README) was reviewed from four
angles. These findings are open and are the starting list of the next session. Severity as the
reviewers ranked it; each item names where to look and a suggested fix.

The UI items are the planned migration of `apps/ui` from the pinned contract 1.1.0
(`apps/ui/contract-pin/`) to the API's contract 1.5.0.

## Rules and legal behaviour

- **[minor] No message states a refund after its deadline: a late rejection or standard-terms act sends none**  
  Where: llm/phrasebook.py (_REGISTERED, _REGISTERED_AMOUNT and _DEDUCTION all assume {deadline}); apps/api/schemas.py late_without_message; ADR-15 section 20  
  Fix: Add a reviewed withdrawal body without {deadline} (with {amount} and the {deduction} sentence), in Italian and English, and send it on a late act instead of no message. Left open in contract 1.8.0: it is new customer wording; until then such a case stays refund_overdue in the queue and the merchant states the refund outside ReturnRing.

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
- **[major] [UI migration] The mock router does not serve the 1.5.0 example table: .sse streams, fixture scenarios that hide contract examples, and a role check that pre-empts them**  
  Where: UI @5af3135: src/api/adapters/mockRouter.ts:268-297,380; src/api/adapters/fixtures.ts:5-11; src/api/adapters/mock.ts:9 (glob of *.json only)  
  Fix: For a text/event-stream route, load the .json sibling as the event list. Drop the UI fixtures that the contract now covers and keep only draft_pending, agent_error and contract_mismatch. Before the fixture role check, look for a contract route whose request body matches, and add approver to the decision BODY_KEYS.
- **[major] [UI migration] Public read-only demo: the bundle never learns it is read-only, so its write buttons only produce 403s**  
  Where: UI @5af3135: apps/ui/Dockerfile (no ARG VITE_READ_ONLY); src/components/feedback/roles.ts:14,20; shop confirm (src/api/mutations.ts:82, ShopPage); src/views/agent (cached-mode comment); src/api/client.ts:100. Branch 5156e3a: infra/docker-compose.yml:128-130, infra/compose.demo.yml:31-34  
  Fix: Read readyz.public_read_only at runtime and gate every write on it, except agent ask and checkout evaluate. Keep `ARG VITE_READ_ONLY=false` in the Dockerfile only as a fallback. Treat a 403 while public_read_only is true as the read-only notice. Gate the shop confirm. In cached mode show the agent as unavailable. Change the 401 text to /system.
- **[major] [UI migration] Every cached draft and agent answer says "Precomputed on MI300X", whatever readyz.label says (rules 11 and 13)**  
  Where: UI @5af3135: src/copy/common.ts:120 (MESSAGE.generation.cached), src/components/message/GenerationBadge.tsx:11, src/views/agent/copy.ts:64 and 133  
  Fix: Take the cached wording from readyz.label: "Precomputed on AMD MI300X" only when /readyz shows it, the rehearsal label "Precomputed in a rehearsal, not verified on AMD MI300X" otherwise; never a fixed MI300X string. Since contract 1.5.2 the API's cached telemetry note names no hardware (ADR-15 section 17).
- **[major] [UI migration] The demo clock is ignored: the UI never shows readyz.clock and dates some Metrics with the browser clock**  
  Where: UI @5af3135: src/components/mode/useModeView.ts, ModeBanner; src/views/platform/shop/ShopPage.tsx:73; src/views/platform/demo/DemoPage.tsx:31; src/views/platform/demo/preflight.ts:62  
  Fix: Show kind, now and snapshot from readyz.clock in the mode banner, advancing it with (Date.now() - fetchedAt) and re-reading readyz. Take Metric periods from that clock. Mark telemetry times as wall time. In preflight, accept vllm 'disabled' when readyz.llm_mode is mock.
- **[major] [UI migration] Error statuses and codes the UI must handle against 1.5.0, including a photo limit above the contract**  
  Where: UI @5af3135: src/views/operator/case/sheets/*, src/views/platform/shop/*, src/views/manager/kpi, src/views/platform/constants.ts:11 (PHOTO_MAX_MB=8), src/views/platform/inspection/inspection.ts:64  
  Fix: Map each status above to its notice and recovery: refetch on stale_proposal and stale_decision, re-evaluate on checkout 409, empty states on 404 no_report and no_demo_reset. Set the photo limit to floor(7,000,000 × 3 / 4) bytes, read from the schema's maxLength.
