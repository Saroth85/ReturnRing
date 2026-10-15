# Open review findings (14 Oct 2026)

The last integration round (API contract 1.5.0, deployment, README) was reviewed from four
angles. These findings are open and are the starting list of the next session. Severity as the
reviewers ranked it; each item names where to look and a suggested fix.

The eight [UI migration] items (contract 1.1.0 to 1.5.0) are closed: the UI runs on contract 1.8.0
from `contracts/` (no pinned copy), and `npm run test:e2e:live` walks the demo path against the
live API. Two UI follow-ups stay open below.

## Rules and legal behaviour

- **[minor] No message states a refund after its deadline: a late rejection or standard-terms act sends none**  
  Where: llm/phrasebook.py (_REGISTERED, _REGISTERED_AMOUNT and _DEDUCTION all assume {deadline}); apps/api/schemas.py late_without_message; ADR-15 section 20  
  Fix: Add a reviewed withdrawal body without {deadline} (with {amount} and the {deduction} sentence), in Italian and English, and send it on a late act instead of no message. Left open in contract 1.8.0: it is new customer wording; until then such a case stays refund_overdue in the queue and the merchant states the refund outside ReturnRing.

## UI follow-ups (after the migration to contract 1.8.0)

- **[minor] [UI] No sentence picker: the approve, override and reject sheets send `sentence_ids: null`**  
  Where: apps/ui/src/views/operator/case/sheets/DecisionSheets.tsx, src/views/operator/state/requests.ts; docs/ui-design.md section 2 (PhrasebookPicker, a Could)  
  Fix: Offer the phrasebook sentences of `draft.message.sentence_ids` (2 to 5 ids) in the sheets and send the chosen ones. Open: the service uses the draft's own selection meanwhile, which is valid.
- **[minor] [UI] The demo path has no inspection beat: the demo cases name no return awaiting inspection**  
  Where: apps/ui/src/views/platform/demo/beats.ts; `DemoCaseKey` in apps/api/schemas.py (GET /api/demo/cases)  
  Fix: Add an inspection key to the demo cases (a withdrawal return the mock vision reads as damaged) and a beat for Ahmed. Open: /inspection is one click away in the meantime.
