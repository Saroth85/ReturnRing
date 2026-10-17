# Known open findings (17 Oct 2026, contract 1.9.0)

These are the accepted limits of the submission: review findings that stay open, and what the
checks did not verify. Severity as the reviewers ranked it; each item names where to look and a
suggested fix.

The list started from the review of 14 Oct 2026 (API contract 1.5.0, deployment, README, from
four angles). Its other findings were closed by contracts 1.5.1 to 1.8.1 (ADR-15 sections 16 to
21, ADR-17 amendments) and its eight [UI migration] items by the UI's move to the generated
contract: the UI runs on contract 1.9.0 from `contracts/` (no pinned copy), and
`npm run test:e2e:live` walks the demo path against the live API.

## Rules and legal behaviour

- **[minor] No message states a refund after its deadline: a late rejection or standard-terms act sends none**  
  Where: llm/phrasebook.py (_REGISTERED, _REGISTERED_AMOUNT and _DEDUCTION all assume {deadline}); apps/api/schemas.py late_without_message; ADR-15 section 20  
  Fix: Add a reviewed withdrawal body without {deadline} (with {amount} and the {deduction} sentence), in Italian and English, and send it on a late act instead of no message. Still open in contract 1.9.0: it is new customer wording; until then such a case stays refund_overdue in the queue and the merchant states the refund outside ReturnRing.

## UI follow-ups

- **[minor] [UI] No sentence picker: the approve, override and reject sheets send `sentence_ids: null`**  
  Where: apps/ui/src/views/operator/case/sheets/DecisionSheets.tsx, src/views/operator/state/requests.ts; docs/ui-design.md section 2 (PhrasebookPicker, a Could)  
  Fix: Offer the phrasebook sentences of `draft.message.sentence_ids` (2 to 5 ids) in the sheets and send the chosen ones. Open: the service uses the draft's own selection meanwhile, which is valid.
- **[minor] [UI] The demo path has no inspection beat: the demo cases name no return awaiting inspection**  
  Where: apps/ui/src/views/platform/demo/beats.ts; `DemoCaseKey` in apps/api/schemas.py (GET /api/demo/cases)  
  Fix: Add an inspection key to the demo cases (a withdrawal return the mock vision reads as damaged) and a beat for Ahmed. Open: /inspection is one click away in the meantime.

## Verification limits

- **The counter-lock regressions run on SQLite only.** The two regressions of the main review
  (a catalogue task, and a checkout confirmation with a coupon, each committed by a competing
  request while the first one waits for the shared counter; tests/test_api_manager_catalog.py
  and tests/test_api_other_checkout.py) use two real SQLite transactions with the competing
  commit at the allocator boundary. They reproduce the PostgreSQL READ COMMITTED visibility
  window but do not exercise PostgreSQL row locking, and they were not run on PostgreSQL (the
  review had no PostgreSQL test URL). The PostgreSQL suites, `tests/test_api_*_postgres.py`
  (the concurrent writers of the fifth API review among them), run only when
  `RETURNRING_TEST_POSTGRES_URL` is set: CI has no PostgreSQL service and does not set it, so
  they skip there (.github/workflows/ci.yml).
- **The CPU suite does not collect on Windows.** `pipeline/ingest/cli.py` imports the POSIX-only
  `resource` module, so `make test` stops at collection on a Windows host (found in the main
  review of 17 Oct 2026). Linux CI remains the complete-suite gate.

## Closed reviews

- **Main review of 17 Oct 2026** (base `31c97d1`, merged as PR #16 in `dac3a3a`, fixes in
  `b47db27`): nine findings fixed, three P1, five P2 and one P3. Export destinations that
  overlap a source tree are refused before any change; catalogue tasks and checkout
  confirmations check after taking the existing counter lock; graph files are reused only when
  their lineage derives from the current store; a malformed local LLM authority fails at
  configuration with a sanitized error; token counts are validated the same way for text and
  vision; the KPI abstention link opens the insufficient-evidence tab; reviews and inspections
  page past the first 25 returns; a case opened with the mouse loses its New badge. Its open
  limits are the two verification limits above. The review note was removed on 17 Oct 2026; it stays in
  the git history (added by `b47db27`).
