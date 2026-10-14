# Open review findings (14 Oct 2026)

The last integration round (API contract 1.5.0, deployment, README) was reviewed from four
angles. These findings are open and are the starting list of the next session. Severity as the
reviewers ranked it; each item names where to look and a suggested fix.

The UI items are the planned migration of `apps/ui` from the pinned contract 1.1.0
(`apps/ui/contract-pin/`) to the API's contract 1.5.0.

## Rules and legal behaviour

- **[major] Public cached demo: the demo clock keeps running after the reset, so every open return shows its refund as overdue within 14 days**  
  Where: apps/api/clock.py DemoClock.now (anchor plus unbounded elapsed time); apps/api/runtime.py refresh_clock (re-anchors only after a new reset row); infra/compose.demo.yml (reset is one-shot at make demo-up, DEMO_CLOCK defaults to on); apps/api/services/operator/queue.py:279 (refund_overdue)  
  Fix: With PUBLIC_READ_ONLY or DEMO_MODE=cached, freeze the demo clock or cap the elapsed time (for example DemoClock(max_elapsed=1 day)). There are no writes in that mode, so a frozen clock stays coherent. Alternatively, schedule a daily `make demo-reset` on the demo server and document it in the runbook and README.
- **[major] The 14-day withdrawal refund deadline blocks and flags guarantee-path cases, whose message states no deadline (rule 4, NFR-17)**  
  Where: policy/engine.py:152 (refund_due_by set on every path); apps/api/services/outbox.py:27 check_refund_due, called unconditionally at apps/api/services/operator/actions.py:239 and apps/api/services/other/inspections.py:786; apps/api/services/operator/queue.py:279  
  Fix: Skip check_refund_due, and set refund_overdue to false, when view.path is guarantee, or more generally whenever the composed message has no {deadline} field. Do not present refund_due_by as the deadline of a guarantee case.
- **[minor] After refund_due_by, a pending Q3/Q4 restriction can be neither approved nor rejected; the stale-proposal recompute always ends in that 409**  
  Where: apps/api/services/operator/actions.py:239 (check_refund_due runs before approve, override and reject); stale path actions.py recompute_stale and routes/operator.py post_decision; encoded in tests/test_api_round5.py:544  
  Fix: Allow reject and override-to-standard after the deadline, with a message composed without the {deadline} field or with no message. At minimum, exempt reject from check_refund_due.
- **[minor] Rule 11: draft facts and agent answers label score and value figures with the decision day, not the customer snapshot the engine used**  
  Where: apps/api/services/generation.py:122 (build_case is called without as_of, so llm/case.py:196 defaults to decided_on); apps/api/services/other/agent.py:287 (as_of=view.decided_on)  
  Fix: Add the customer snapshot instant (Proposal.context.as_of) to DraftInputs and pass build_case(as_of=<snapshot day>), and use the same date in ApiCaseReader. This changes the llm_case digest, so the batch cache has to be regenerated.
- **[minor] Rule 11: the refund amount in outbox messages carries the provenance of the diminished-value claim**  
  Where: apps/api/services/operator/actions.py:326 and apps/api/services/other/inspections.py:845 (provenance=claim.amount.provenance)  
  Fix: Give the refund its own provenance: 'price paid for the returned line (store/order_lines, store/orders) less the claim of INS-...', with the period and nature of the line price.
- **[minor] Rule 11: the checkout coupon block tells every linked account that its own previous returns caused the block**  
  Where: llm/phrasebook.py:558 COUPON_BLOCK_TEXT (reuses _NOTICE_AFTER_TERMS); apps/api/services/other/checkout.py:257  
  Fix: Use a reviewed neutral sentence for the cluster block that attributes no cause (for example 'Discount codes cannot be used on this order until <review date>'). Keep 'your previous returns' only for the customer of the approved case.

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
- **[major] [UI migration] Every cached draft and agent answer says "Precomputed on MI300X", whatever readyz.label says (rules 11 and 13)**  
  Where: UI @5af3135: src/copy/common.ts:120 (MESSAGE.generation.cached), src/components/message/GenerationBadge.tsx:11, src/views/agent/copy.ts:64 and 133  
  Fix: Take the cached wording from readyz.label: "Precomputed on AMD MI300X" only when /readyz shows it, the rehearsal label "Precomputed in a rehearsal, not verified on AMD MI300X" otherwise; never a fixed MI300X string. Since contract 1.5.2 the API's cached telemetry note names no hardware (ADR-15 section 17).
- **[major] [UI migration] The demo clock is ignored: the UI never shows readyz.clock and dates some Metrics with the browser clock**  
  Where: UI @5af3135: src/components/mode/useModeView.ts, ModeBanner; src/views/platform/shop/ShopPage.tsx:73; src/views/platform/demo/DemoPage.tsx:31; src/views/platform/demo/preflight.ts:62  
  Fix: Show kind, now and snapshot from readyz.clock in the mode banner, advancing it with (Date.now() - fetchedAt) and re-reading readyz. Take Metric periods from that clock. Mark telemetry times as wall time. In preflight, accept vllm 'disabled' when readyz.llm_mode is mock.
- **[major] [Examples vs live] The Giulia checkout example shows a specific size tip and size-dependent probabilities that the live service never produces**  
  Where: scripts/build_api_examples.py:2963 (SizeAdvice.of('specific','M')); apps/api/services/other/checkout.py:168-175; ml/reference/checkout.py:29-50  
  Fix: Build checkout_evaluate_response.giulia through the live size_advice and model (measurement guide, equal p for both sizes), or remove the 'specific' kind from the example set. Make the UI shop beat show the measurement-guide text and drop 'Switch to M'.
- **[major] [UI migration] Error statuses and codes the UI must handle against 1.5.0, including a photo limit above the contract**  
  Where: UI @5af3135: src/views/operator/case/sheets/*, src/views/platform/shop/*, src/views/manager/kpi, src/views/platform/constants.ts:11 (PHOTO_MAX_MB=8), src/views/platform/inspection/inspection.ts:64  
  Fix: Map each status above to its notice and recovery: refetch on stale_proposal and stale_decision, re-evaluate on checkout 409, empty states on 404 no_report and no_demo_reset. Set the photo limit to floor(7,000,000 × 3 / 4) bytes, read from the schema's maxLength.
- **[minor] [API] The catalogue-task check is weaker than ADR-15 H14, and the live Elena card's anomaly does not contain her return**  
  Where: apps/api/services/manager/catalog.py:260-272 (open_task); apps/api/services/cases.py:820-846 (card anomaly)  
  Fix: Decide the semantics. Either enforce H14 (404 unless the return is in the anomaly's queued ids), or keep it accepting and document that any queued return of the variant may be named, adding the case's own return to the card's queued_return_ids. The second keeps the Elena beat working.
- **[minor] [Examples vs live] The inspection examples contradict the live mock vision for their own photo**  
  Where: scripts/build_api_examples.py:3202,3669; apps/api/services/other/inspections.py:229-236 (mock_outcome), 704-712 (no_proposal)  
  Fix: Build the inspection examples through MockVision. Pick photo bytes with sha256[0] % 3 == 1 (damaged) for the awaiting case, and 2 or unreadable for manual review. Document that the claim beat needs LLM_MODE=mock or vllm.
- **[minor] [Examples vs live] The demo's evidence and dispute beats change nothing live; model_versions keys and the Marco checkout state differ from the examples**  
  Where: apps/api/services/demo.py:106-140 (find_dispute); scripts/build_api_examples.py MODEL_VERSIONS (around 813, 4288); checkout_evaluate_response.marco example  
  Fix: In find_dispute, prefer a link whose recompute changes quadrant or levers (dry-run the candidates), or have the UI say 'decision unchanged'. Generate the example MODEL_VERSIONS from the serving manifest's key set. Order the live pitch so Marco is approved before the shop beat.
