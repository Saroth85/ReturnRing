# ADR-14: LLM contracts v2

Status: accepted, 13 Oct 2026; amended the same day after the second round of the adversarial
review (decision 8, the customer phrasebook) and after the third (decision 9, operator statements
without levers), and on 13 Oct after the fourth (decision 10, the last hardening round before the
demo) and after the fifth (decision 11, precision over recall, the closing round; amended after
the closing verification, round 6). Changes `contracts/json/llm_case.schema.json`,
`contracts/json/llm_output.schema.json`, their examples, `llm/` and spec sections 8.7, 8.8, 19.B,
19.C and 19.D. Resolves review findings LLM-1 to LLM-14 of 12 Oct (US-304, US-305), findings R1
to R8 of the second adversarial review of 13 Oct, findings T1 to T16 of the third, findings U1 to
U19 of the fourth, findings V1 to V18 of the fifth and findings W1 to W7 of the closing
verification.

## Context

The v1 LLM contracts had four problems.

- **Grounding on a bag of numbers (LLM-3, LLM-4).** The operator explanation was one string. The
  validator accepted any number found anywhere in the case. That included the customer's own text,
  the policy text and digits inside identifiers. "7 accounts" passed even when 7 was the number of
  coupons and not the cluster size.
- **No link between a statement and its evidence (US-305).** The labels OBSERVED, SCORE and
  HYPOTHESIS were free text. Nothing tied a sentence to the entity, metric, value and period it
  talks about.
- **Placeholders without a rule (LLM-9).** The model could not know which of {terms}, {amount}
  and {deadline} had a value for this decision. The validator and the renderer each had their own
  grammar.
- **Scores where they must not be.** The schema required `risk` and `value_eur` on every case,
  also on the guarantee path, which is never scored (rule 4). The same held for cases with
  insufficient evidence, which must look neither safe nor suspicious (US-307).

## Decision

### 1. Facts built by code (llm_case)

`facts` is a list of `{id, entity, metric, value, period, nature}`, built by `llm/case.py` from
the engine decision, the request and the scores.

| Field | Content |
| --- | --- |
| id | `F1`, `F2`, ... in order |
| entity | customer, return, order or cluster |
| metric | snake_case name: risk_score, value_eur_12m, a score factor feature, cluster_size, days_since_delivery |
| value | a JSON number, rounded half-up to 2 decimals |
| period | for example "as of 2026-10-14", "12 months to 2026-10-14", "2026-10-08/2026-10-14" |
| nature | real, synthetic or simulated (rule 11) |

These are the only numbers the explanation may use. The customer text, the policy text and the
identifiers never feed grounding. The levers are not facts: since decision 9 a statement does not
talk about them, so the former `policy` entity and its `return_window_days` fact are gone.

### 2. Statements that cite facts (llm_output)

`operator_explanation` becomes an array of statements, each `{label, fact_ids, text}`:

- `label` is OBSERVED, SCORE or HYPOTHESIS;
- every number in `text` must equal the value of one of the facts it cites (decimal comma
  accepted, no rounding, no conversion, no number words or Roman numerals), with an allowed
  context around it (decision 9);
- a SCORE statement cites at least one fact;
- an OBSERVED statement never cites `risk_score`, because a model output is not an observation;
- known identifiers (case id, cluster id, metric names, Q1 to Q4) are removed before numbers are
  read. They are never accepted as values.

The UI shows the entity, metric, period and nature of each cited fact next to the statement, so
the model writes no dates or periods.

### 3. Placeholders (llm_case)

`placeholders` is `{available, required}`, computed by the one serializer in `llm/decision.py`:

- {terms} is available when the levers change the terms of future orders;
- {deadline} is available on the withdrawal path;
- {amount} is available on the withdrawal path when the refund amount is known;
- {terms} is required when the decision has restrictive levers and terms to show.

`llm/messages.py` holds the one grammar that `validate()` and `render()` share. The only tokens
are those three placeholders. Any other brace, bracket or angle character is rejected.

### 4. Closed, bounded schemas for guided decoding

Both schemas close every object (`additionalProperties: false`) and bound every list and string:

- at most 8 statements of 300 characters;
- at most 4 fact ids of 4 characters each;
- at most 9 lever ids of 40 characters each;
- a customer message of 2 to 5 phrasebook ids (decision 8), from a closed enum.

There is no regex `pattern` in the output schema, so guided decoding does not depend on regex
support. The validator checks fact ids and lever ids against the case. `llm/validator.py`
mirrors the output schema in pydantic, and a test keeps the two equal.

### 5. Scores only where they belong

The llm_case schema has three conditions:

- guarantee path: no `risk`, `value_eur` or `cluster`; no quadrant, lever or placeholder;
- insufficient evidence: no `risk`, `value_eur` or `cluster`; no quadrant or lever;
  `missing_evidence` is required;
- withdrawal with sufficient evidence: `risk` and `value_eur` are required, and so is a quadrant.

`customer_text` must match `^<customer_text>[^<>]*</customer_text>$`. The builder escapes `&`,
`<` and `>`, removes control and format characters and breaks the tag name, so the text cannot
close its delimiter.

### 6. Validator actions (LLM-13)

Every error is `{code, action, where, detail}`, and `output` is None when there is any error. The
actions follow the table of spec 8.7:

| Action | Codes |
| --- | --- |
| TEMPLATE | DECISION_ECHO |
| BLOCK | CASE_INVALID: the case does not match the engine decision, a code bug |
| REGENERATE (then template) | every other code: SCHEMA, UNKNOWN_FACT, MISSING_FACT, LABEL_MISMATCH, UNGROUNDED_NUMBER, BANNED_TERM, SCRIPT, SENTENCE_ID, WRONG_LANGUAGE, TERMS_TOPIC |

A missing or altered legal clause is checked on the rendered message by
`llm/legal_clause.assert_clause`, which raises `LegalClauseError` (blocking). Since decision 8 the
placeholders are placed by code, so a placeholder error in `llm/messages.render` is a code bug
too (`MessageError`).

### 7. Guarantee clause (LLM-11)

The v1 guarantee clause listed refund as a free choice next to repair and replacement. The
proposed draft follows Directive (EU) 2019/771, art. 13 and 14:

- repair or replacement first, free of charge, within a reasonable time;
- price reduction or termination where the law provides for it;
- never termination for a minor defect.

It is appended always on the guarantee path. It stays a DRAFT pending D-14. It is in spec 19.C,
and the LEG-06 test compares the code with it.

### 8. The customer message comes from a phrasebook (amendment, round 2, finding R1)

The second round of the adversarial review showed that a blocklist cannot keep free customer
prose safe. Paraphrases with no listed word still denied the refund ("Non riceverai alcun
rimborso"), decided the remedy on the guarantee path ("We will give you your money back"),
contradicted the {terms} ("Il reso lo paghiamo noi") or stated a decision ("Your return is
okayed"). Each new word list would only move the gap.

So Gemma no longer writes customer prose:

- `llm/phrasebook.py` holds 19 reviewed connective sentences. Each has a stable id (OPEN_THANKS,
  HELP_SIZE, CLOSE_VALUED...), fixed Italian and English texts, a slot (opening, extra, closing)
  and the decision kinds where it is allowed (guarantee, insufficient, Q1 to Q4), with lever
  conditions (HELP_SIZE is ruled out when the free size exchange is withdrawn; INFO_TERMS needs
  terms to talk about) and coherence rules (decision 9). The sentences are warm and neutral
  (spec 16), with no number, amount, deadline, term, legal wording or decision word.
- `customer_message` is `{"sentence_ids": [...]}`, 2 to 5 ids from a closed enum: one opening, at
  most three extras, one closing.
- Code writes the message per language: opening, the mandatory body, the extras, the closing,
  then the legal clause (`render`). The body is built from the engine decision and never chosen
  by the model: the registration with {deadline} (and {amount} when known), the {terms} sentence
  when future terms change, the standard-terms sentence for insufficient evidence, the
  transparency notice (Q3, Q4) and the guarantee registration. Placeholders, `render`,
  `required_clause` and `assert_clause` work as before.
- The validator turns an id that is repeated, in the wrong slot or not allowed for the decision
  into SENTENCE_ID (regenerate, then template). An unknown id fails the enum: SCHEMA.
- The fallback template is a default id selection per decision kind. The mock model uses it.
- `llm/prompts.py` lists, after the case, only the ids allowed for the case with their English
  text. `VllmClient` sends a per-case schema whose `sentence_ids` enum is narrowed to those ids;
  `decision_echo` stays open, so a misread decision is still caught as DECISION_ECHO.
- The text checks that only served the customer message (literal numbers and units, legal,
  terms and decision wording) stay in `llm/text.py` as the review gate of the phrasebook:
  `phrase_problems` runs them on every sentence and on every pair of parts that can stand side
  by side, in both languages. Tests render every allowed selection for every kind of engine
  decision, on four merchant bases, with and without a known refund: the required clause is
  there once, amounts, terms and deadlines are the template fields, the language is right and
  the message stays within 120 words before the clause.

What Gemma still writes: the operator statements (`operator_explanation`) and the answers of the
investigator agent (`llm/agent.py`). Both keep the text checks, hardened in the same round:

- R2: Q(I-IV) is a quadrant label. Lever values read through negations, a quadrant described
  by its levels and restrictions were checked against the engine; decision 9 replaced that
  reader with a topic check, because paraphrases kept passing it (round 3, finding T7).
- R3: more same-person synonyms (consumer, purchaser, identity, household with a qualifier...),
  up to two words between qualifier and noun, and ownership of all the accounts ("the customer
  owns all the linked accounts", "sono tutti tuoi").
- R4: the agent checks each sentence of a question, with digits read as letters and dashes
  joined, and more verbs (accept, blacklist, Italian first person plural...). An answer with an
  imperative, an obligation ("must be disabled") or a claim of an action ("Done", "now off")
  falls back. A number in a clause that names a record (a link, a cluster, an order, the case)
  must be a value of that record.
- R5: a script allowlist, new code SCRIPT (regenerate) in a statement and a fallback in an
  answer. Decision 9 moved it to the raw characters (finding T9).
- R6: a short cardinal glued to an abbreviated unit ("tenhrs", "dueh") and slang amounts
  ("tenner", "sennight") are numbers; abbreviations, holidays, periods and relative days are
  units in the phrasebook gate.
- R7: a sign after spaces ("- 42.1"), parentheses ("(42.1)"), plus-minus, a trailing dash, more
  scale and unit words ("grand", "kEUR", "percent", "bps") and sign words ("owed", "debt",
  "shortfall") make a number ungrounded.
- R8: `check_local_url` drops fc00::/7, because the compose network is IPv4 only; this refuses
  the EC2 IPv6 metadata endpoint fd00:ec2::254.

### 9. Operator statements without levers, and the third review (amendment, round 3)

The third round of the adversarial review (findings T1 to T16) found that reading lever values
from words would not converge: "Refunds are instant", "Promo codes are enabled", "There is no
restriction on instant refund" passed, and the same reader rejected correct statements ("The
return window is reduced to 14 days") and sent them to the template. The maintainer decided:

- **Topics (T7, T8).** An operator statement does not talk about levers, return terms, refunds,
  discounts, restrictions, decisions or the risk and value levels of the quadrant at all: the
  interface shows the engine decision and its levers from code. `text.operator_topics` runs the
  terms stems of the customer gate plus restriction words (restrict, strict, limitation,
  penal, withh...), refunds and their paraphrases (repay, reimburse, money back, postage,
  prepaid, store credit, gift card), promo, perks, "lever", "terms", "policy", size advice,
  decision words, axis levels ("risk level is low", "il rischio è molto basso", "below the
  threshold", "above average", "significant", "risky") and places in the matrix, after removing
  metric names and the observed phrases "welcome coupon(s)", "free text", "in terms of" and "in
  quick/rapid succession". A hit is the new code TERMS_TOPIC: regenerate, then template.
  `validator.lever_claims` is gone. DECISION_ECHO stays for a quadrant label other than the
  engine's (also "Q-one") and for a level of the risk score other than the engine's ("the risk
  score is low" in Q4; TERMS_TOPIC since decision 11, round 6); the level of the risk score, of
  an order or of an item is not a quadrant level ("The risk score is high (0.87)", "This is a
  high-value order" pass). The templates no longer name levers or terms.
- **Contract change.** The `return_window_days` fact (entity `policy`) could only be used to talk
  about a lever, so `llm/case.py` no longer builds it and the `policy` entity left the facts of
  `llm_case.schema.json`; the example, spec 19.B and the tests follow.
- **Grounding context (T11, T8).** A number of a statement is grounded only by a cited fact of the
  same value, sign included, in an allowed context (`text.grounding_problems`). After it: the
  end, punctuation, a spaced dash, a function word, or the unit of the metric (EUR, euro or €
  for `*_eur*`; day(s) or giorni for `*_days`; for scores, rates and counts no unit or a count
  noun), followed in turn by the end, punctuation or a function word (a count noun by anything
  but a rate word). Before it: the start, a word that is no comparator and no unit of another
  kind, a separator, "(" after a word, or a dash with spaces around it after a word ("Customer
  net margin — 42.1 EUR — is low", "Linked accounts in the cluster - 7"). Any other dash before
  the digits is a sign. A sign word is allowed only when every number carries the sign of a
  negative fact ("negative: -42.1 EUR"). The agent keeps its stricter rule: any dash before a
  number is a sign.
- **Numbers in other forms (T10).** -fold, -ish and -s after a cardinal ("sevenfold",
  "twelvish", "sevens"), multiplicatives (doubled, quadrupled, septuple), quantity words
  (couple, pair, handful, trio, grand, quid, buck), French, Spanish and German cardinals up to
  twelve, and upper-case canonical Roman numerals of two or more letters ("VII", "XLII"; "XL" is
  a size) are numbers, in statements and answers.
- **Characters (T9).** The allowlist runs on the raw text, before NFKC: printable ASCII, tab and
  line breaks, the Italian accented vowels and ’ ‘ “ ” – — … € and the no-break space. Enclosed,
  squared, full-width and mathematical letters, regional indicators and symbols such as ° ○ ∀
  are SCRIPT; so is the minus sign U+2212.
- **Ownership (T6).** In any sentence about the linked accounts (or a cluster), an ownership or
  agency word is a banned term, without the former "all" condition: own(s), owned, controls,
  operates, runs, manages, behind, "<verb>ed by", an active past with an object ("created the
  linked accounts"), belonging to anything but a cluster or group, tracing back, "the customer's
  accounts", "accounts of the customer", Italian possiede, controlla, gestisce, riconducibili,
  fanno capo, appartengono, intestati, del cliente, suoi. "Its own", "their own" and a
  participle without an agent ("created within 9 days") stay allowed. Actor, operator, someone
  join the person nouns; "one and only" and "share an owner" are same-person phrases.
- **Agent questions (T12, T15, T16).** Sentences split only at a terminator that is not a dot
  between two letters, so "dis.able" stays one word. Each clause (split at , : ; dashes and
  and/then/so/also/e/poi/quindi/anche, before accents are folded) that opens with a write verb
  is a request, whatever question word came before. Italian clitics are stripped ("Approvalo",
  "Disattivali", "disattivarli"), formal imperatives count at the start of a clause ("Approvi",
  "Disattivi", "Tolga"), and new verbs are known (freeze, lock, restrict, lift, release, process,
  drop, whitelist, authorize, autorizza, emetti, riduciamo...). A write verb right after a
  determiner or preposition, or before a noun it qualifies ("status", "deadline", "history",
  "of"), is a noun: "Tell me about the refund", "Refund status?" and "Show the blocked accounts"
  are answered. Italian infinitives stay verbs ("Ricordati di spegnere").
- **Agent answers (T13).** Besides imperatives and modals: a first-person subject ("I" or "we"
  before an auxiliary or a write form, "ho", "abbiamo") with a write verb, participle or past
  anywhere after it in the sentence; completion cues (as requested, carried out, went ahead,
  all set, come richiesto, provveduto, riuscito, "now been disabled"); obligation cues ("to be"
  with a participle, "da" with a write infinitive, andrebbe, conviene, need/require + -ing,
  make sure, ensure, assicurati, fai in modo, ricordati di, and the -ino/-ano subjunctives).
- **Agent grounding (T14).** A number next to a field word must be that field of a record in
  scope: linked accounts (size), distinct cards, shared events, weight, days (any `*_days`
  field), return rate. A clause with only a kind word ("the cluster", "the disputed link") is
  scoped to the records of that kind, when the results have one; a link is also named by its two
  accounts; a sentence that opens with a pronoun (it, its, they, esso) keeps the scope of the
  one before. When the scope is one record, every field-bound number must come from the same
  record ("The disputed link has weight 0.6 and 4 shared events" falls back).
- **Phrasebook (T1 to T5).** The transparency notice refers to future orders only: right after
  the {terms} sentence it says "These terms take into account the returns on your previous
  orders"; alone (the base already lacks what Q3 or Q4 would withdraw, for example
  ReturnTerms(30, True, False, True, True) in Q3) it says "The return terms of your next orders
  take into account the returns on your previous orders", so it never reads as if the current
  refund depended on past returns (rule 3). The Italian registration says "al più tardi per il
  {deadline}", a limit like the English "by". Insufficient evidence states the engine decision,
  "Your next orders have our standard return terms" since decision 10 ("keep" claimed unchanged
  terms that code cannot know).
  A selection has at most one sentence that asks for a reply (HELP_SIZE, CONTACT_REPLY,
  INFO_TERMS, GUARANTEE_PHOTO) and CLOSE_DAY ("Thanks again") only after a thanks opening; the
  prompt lists these rules with the sentences. The word counts below are the ones the tests
  measure.

### 10. The fourth review: the last hardening round before the demo (amendment, round 4)

The fourth round (findings U1 to U19, a re-review of b9a7b87) found one flaky test, three legal
wording gaps in the customer message and many paraphrases that still passed the free-text checks.
Each probe of the review is a regression test in `tests/test_llm_review4.py` that fails on b9a7b87,
and each over-blocked example is a must-accept test there.

- **A deterministic suite (U1).** A closing parenthesis was a closer, so "(42.1) mil", "(42.1)
  USD" or "(42.1 EUR) per day" were grounded. It is now transparent: what follows it must be
  allowed after the number. The property test that found it failed only once its example was in
  the `.hypothesis` database. `tests/conftest.py` loads a pinned Hypothesis profile, "ci", with
  `derandomize=True` (a seed derived from each test, no example database), so every run tests the
  same examples, with or without a `.hypothesis` directory. Every failing example found so far is
  an explicit `@example`. `HYPOTHESIS_PROFILE=explore` runs random examples to look for new ones:
  24 seeds and a run of 20,000 examples of the grounding property found none.
- **Customer message (U2, U3, U4; rule 3, spec 8.6).** In Q3 and Q4 the body says, right after the
  registration, "This return keeps the terms of your order." ("Questo reso mantiene le condizioni
  del tuo ordine."), so the new terms of the next orders are never read as applying to the current
  return. To keep 120 words, Q3 and Q4 take at most two extras: `selection_problems` refuses a
  third, the per-case schema sends `maxItems` 4 for them, and the prompt says so. Insufficient
  evidence says "Your next orders have our standard return terms." ("hanno", not "keep" or
  "mantengono"). The {terms} sentence presents new terms as a consequence of past returns ("Your
  next orders will have new return terms... These terms take into account...") only when a
  restrictive lever has a phrase; a benefit alone, such as the free size exchange on a base
  without it, gets the neutral sentence and the notice that names the next orders. The tests sweep
  the 48 bases, every Q3 and Q4 scenario and both refund variants. The longest message is still
  120 words in Italian and 116 in English.
- **Levels (U5).** Numbers become a placeholder word before clauses are split, so "The risk score
  (0.87) is low" is read. A negation (not, n't, never, far from, hardly) or a middle level
  (moderate, medium, borderline) is a level of unknown side: TERMS_TOPIC, regenerate, never the
  engine's level and never the template. An axis without a level carries over "and" ("the risk
  score and the margin are both low"). The customer value in other words is a measured level like
  the risk score: margin, profit, spend and "valuable", "profitable" must match the engine's value
  level (DECISION_ECHO otherwise, TERMS_TOPIC since decision 11, round 6; "Customer net margin —
  42.1 EUR — is low" passes in Q4). "Safe", "trustworthy", "reliable" and "dangerous" are levels
  of the risk axis (topics), and "quadrant" without a label next to it is a topic ("the generous
  quadrant"); "no quadrant" is an observation only when the engine assigned none.
- **Lever paraphrases (U6).** Every lever is about future orders and every fact about the past,
  so the scope words (future, next or upcoming orders, from now on, going forward, no longer,
  prossimi ordini, non più) and the state words of a lever (enabled, disabled, available,
  keeps, remains, paused, offered, eligible, priority, switched or turned on/off, gone) are
  topics, and so are money, pay, payouts, swaps, loyalty, rewards, "return period", "send back",
  fit or sizing guidance, tighter, tougher, withdrawn. The observed phrase "welcome coupon(s)" is
  still removed, but after the phrases that withhold it ("no welcome coupon") and with the state
  words still checked, so "Welcome coupons are disabled for the cluster" is a topic on every
  decision kind, guarantee path included, while "7 linked accounts each used the welcome coupon"
  passes.
- **Ownership (U7).** The check runs on the whole statement (split only at . ! ? and line breaks,
  a dot between letters joins a word) and on every letter form, so ":", ";" and inner joiners hide
  nothing. A relation rule replaces the verb list: a person or role noun is flagged as the
  subject of a verb whose object is the accounts ("the customer holds / uses / logs into / set up
  the linked accounts"), as the agent of a passive about them ("registered to", "opened by the
  customer"), as their predicate ("the linked accounts are the customer's"), as a role of them
  ("the owner of", "il titolare degli account") or as a possessive before them ("the customer's
  other linked accounts", "his linked accounts", "in the customer's name", "di sua proprietà").
  Alias, alter ego, sock puppet, alt, dietro and "under ... control" are flagged too. The bare
  intransitive verbs are gone: "The linked accounts operate from a shared address" and "...
  controlled by a shared device" pass.
- **Verdict words (U8).** wardrobing, serial returner, fake (not "fake fur"), bogus, exploit,
  misuse, malicious, deceptive, deceive, untrustworthy, bad actor, gaming the system, illicit,
  illecito, furbetto, approfittatore, impostor, sham (a whole word, not "shampoo"), offender,
  culprit, criminal, illegal, crook and swindle are banned terms.
- **Grounding anywhere in the statement (U9, U10).** A unit, currency name, scale word or upper-
  case ISO 4217 code of another kind than the facts its numbers ground, a currency or percent
  sign but €, a rate word (per, monthly, average...) and an approximation (give or take, more or
  less) anywhere in a statement make it ungrounded ("Weeks since delivery: 6", "Customer net
  margin in USD: 42.1", "CHF 42.1"). A function word after the number followed by more, least,
  average, each... is a comparator ("or more", "at least", "on average", "for each order"), and
  so is a comparator before "of", "to" or "than" ("in excess of", "close to"); near, some,
  estimated, north and upwards join the comparators. In the other direction: a stripped
  identifier leaves the word "id" ("driven by cluster_welcome_coupons (7)" passes), "within"
  is allowed before a day count, a count noun may follow two adjectives ("5 recent returns") or
  "out of", "shipping address" and "free-text" are observed phrases, and the hyphen, the
  non-breaking hyphen, the thin space and the narrow no-break space join the character
  allowlist. Every score-factor phrase of the test cases passes the statement checks with its
  own fact; the returns phrase no longer writes its period ("5 recent returns").
- **Joiners and numbers in words (U11, U12).** Any run of ASCII punctuation but , ; : ! ?
  between two letters joins a word ("fr|aud", "fr/aud", "fr{a}ud"), and the letter forms also
  read ASCII-art letters ("pers()n", "fr/\\ud", "sc^m"). nil, nought, naught, zilch, twain,
  brace, gross (not "gross margin"), umpteen, pony and monkey are numbers.
- **Agent questions (U13, U14, U18, U19).** A write word is read as a noun only in a question: a
  sentence that ends with "?", or after a read word (tell, show, explain, list, what, which...;
  dimmi, mostrami, quale...) in the question. Otherwise a write word anywhere is a cue, as at
  0db8c75: "Withhold the refund", "Get this approved", "Set status to Q1", "Reset status" are
  refused. A participle is an adjective only before a noun ("Show blocked accounts") and a
  question only in a "?" sentence without a request marker ("Coupons disabled?"; "Can the coupons
  be turned off?" stays a request). Italian -ate/-ite forms after a state auxiliary or a noun are
  participles ("Le carte sono state bloccate?"), and a formal imperative counts only before an
  object ("Rimborsi precedenti?", "Blocchi attivi?" are answered). Clauses also split at but, or,
  otherwise, else, afterwards, finally, anyway, instead, ma, o, oppure, altrimenti, infine,
  dopodiché, comunque, invece and brackets; a bare imperative with an object is a request
  wherever it stands unless a subject, an auxiliary, "to" or a noun marker comes right before it
  ("..., if none remove L-102"); "why don't you", "perché non (lo) approvi" and "how about
  approving" are requests. A label before ":" in a question is a topic ("Refund: when is it
  due?"). Format characters are removed and dot runs and ellipses between letters join a word
  before the split ("dis…able", "ap.​prove"). New verbs: hold, withhold, delay, postpone, defer,
  void, clear, pause, decline, refuse, reduce, extend, shorten, reclassify, strip, withdraw,
  escalate, disabilitare, abilitare, riattivare, scollegare, eseguire, effettuare, pagare,
  avvisare, notificare, ritardare, trattenere, rinviare; -mente adverbs, pls and do are softeners.
- **Agent answers (U15, U17, U19).** The first-person test is reversed: "I" or "we" (a real "I",
  not a digit read as a letter) or "ho", "abbiamo" followed by any past form or participle is an
  action unless it is a read verb (checked, found, reviewed, consulted; verificato, consultato,
  letto, visto...): "I've taken", "I put", "Ho chiuso", "Ho messo in pausa", "Ho trattenuto" fall
  back. So do "by me" (not "not by me"), "let me", "fammi", "procedo a" with a write verb, a
  clause that opens with a write -ing form before an object ("Switching off coupons"), an Italian
  first person before an object ("Disattivo subito i coupon"), a causative ("Kindly have coupons
  disabled"), "don't" or "do not" with a write verb, a proclitic before a formal imperative ("Li
  disattivi"), the obligations "va" with a participle, bisognerebbe, occorrerebbe, opportuno,
  "serve" with an infinitive, "it is essential that", "ti chiedo di", "non dimenticare di", and the
  completions "taken care of", "at your request", "off as of now". List markers ("- ", "* ", "• ",
  "– ") at the start of a line are removed before every check, so a bullet is neither a minus
  sign nor a character outside the allowlist.
- **Agent grounding (U16, U17).** A clause that names two records of one kind is split at and,
  while, whereas, but, vs, against, e, mentre, ma, contro, rispetto a, and each part binds its
  numbers to the record it names ("F-ROSSI has 7 linked accounts while R-MARCO has 3" falls
  back). The reference names are aliases of their cluster ("the Rossi family", "la famiglia
  Rossi"), "the case cluster" is the case's cluster, "3 in F-ROSSI" binds a number to F-ROSSI and
  "7 here" to the case cluster. A singular kind word with two records of that kind read ("the
  cluster" after both clusters) grounds no number, except "order", which is the case's order,
  and "link", which may be any one link of the cluster as long as every field-bound number comes
  from the same one ("A link of R-MARCO has weight 0.6 and 4 shared events" falls back); "the
  disputed link" is the disputed one. A sentence that opens with this, that, the same, questo, quel or lo
  stesso keeps the scope of the one before, and the list lines under a line that names records
  and ends with ":" keep its scope; "7 vs 3 linked accounts" under "R-MARCO vs F-ROSSI:" binds 7
  to R-MARCO and 3 to F-ROSSI. A cue may follow up to two adjectives ("9 distinct linked accounts",
  "9 payment cards"), "account", "members" and "membri" are size cues, and a cue after "per" is a
  rate, not a field ("1.0 welcome coupons per account"). The three US-702 questions, in English
  and Italian, are answered by MockAgent, and by VllmAgent (source "model") when the model gives a
  correct answer, plain or bulleted, or the mock's own text.

What stays free text, by design. The operator statements and the agent answers are still text
that Gemma writes, and every check on them is a heuristic: blocklists of words and phrases,
narrowed by the character allowlist, the topic check, grounding on cited facts in their context
and on record fields. They will always miss some paraphrase, and they over-block some plain
wording (see the limits below). What makes this acceptable is where that text goes: an operator
statement and an agent answer reach the operator, never the customer; the customer message is
built from reviewed sentences and code fields only; the interface shows the engine decision, its
levers and every cited fact from code; the agent has no write tool; and every restrictive action
needs a human approver with a reason in the audit log (rule 5). A statement or answer that
slips through can mislead a reviewer, not change a decision or a customer's rights.

### 11. Precision over recall for the operator's free text: the closing round (amendment, round 5)

The fifth round (findings V1 to V18, a re-review of 5d27259) closes the LLM layer. It found that
round 4 had over-blocked correct output on the golden cases and had dropped some catches of
b9a7b87. The maintainer's direction for this round: operator statements and agent answers are
free text read by a person who also sees the engine decision, so their checks must be high
precision. A check that rejects realistic, correct Gemma output on the golden cases is a worse
defect than a contrived paraphrase that slips through.

- **The corpus is the guard.** `tests/test_llm_corpus.py` holds correct operator statements
  (OBSERVED, SCORE, HYPOTHESIS, in English, as the prompt of spec 19.D asks) on the golden cases
  of spec 16: Giulia (Q1, returns for size), Marco (Q4, a ring of 7 linked accounts), the Rossi
  family, the Rossi family with a shared card, Elena (a defect on the guarantee path) and a case
  with insufficient evidence; and correct agent answers to the three US-702 questions and to
  "what is this case about", in English and Italian, plain and bulleted. It has every
  over-blocked example quoted in V3, V4, V5, V11 to V14 and V16 to V18, and a second batch
  written apart from the review probes. Every statement must validate (also eight at a time, as
  one explanation), every answer must keep the model path (`source="model"`), and so must the
  mock agent's answers on every golden case and question, every template, and every phrase of
  the API's feature table and of the test factors with its own fact. A check is added only if
  the whole corpus still passes; when a check rejects a corpus item, the check is narrowed.
  Measured on the second batch: 48 of 52 statements and 19 of 20 answers pass; the five rejected
  write numbers in words ("two sizes", "the second factor", "Two links") or attribute the accounts
  to people ("may belong to members of one family"), as the rules intend, and the retry feedback
  names the word.
- **High-precision catches kept or added.** Each one has probes in `tests/test_llm_review5.py`.
  - V1: case, cluster and quadrant ids are read as a space again, so "Q4 (42.1)" and "R-MARCO –
    42.1 EUR" are what they look like, and the property oracle of LLM-03 agrees with the
    validator. Metric names leave a label word, transparent after a number ("42.1 value_eur_12m
    k" is "42.1 k") and allowed before a number or "(" only after a word that is not a
    comparator, or "by" ("driven by cluster_welcome_coupons (7)"). The falsifying examples are
    `@example`s; 30 seeds of 3,000 random examples each found no failure.
  - V2: a control verb (control, run, manage, operate, own; gestire, controllare, possedere,
    amministrare) whose object is the accounts is flagged whatever the subject ("They control",
    "The family runs", "Marco gestisce"), and so is its passive with any agent but an identifier
    or the merchant ("run by them", "gestiti da Marco"; "controlled by a shared device" passes).
  - V10: a person noun with is, are, remains... and same or identical ("the person is the same",
    "the account holder is the same for C-MARCO-1 and C-MARCO-2"), duplicate accounts, clones and
    multi-accounting are banned terms; "the customer's address is the same" is not.
  - V15: flag, monitor, scrutiny, caution, "no further action", "can be closed" and "warrants a
    review" are topics of a statement (regenerate); predatory, throwaway and burner are banned
    terms, and so are farming and harvesting in a text about accounts or coupons, and "gaming"
    anything ("gaming the welcome coupons").
- **Narrowed rules (the over-blocks).**
  - Ownership (V3, V18): the generic subject rule takes a person noun and a verb of possession
    or use from a short list (has, holds, uses, logs into, set up, registered, opened; usa, ha
    creato...), followed directly by the accounts, determiners and numbers aside; it runs clause
    by clause (split at , ; :) on the forms without digits read as letters. So "The customer's
    cluster has 7 linked accounts", "For this customer, 7 linked accounts share a device", "The
    customer belongs to a cluster of 7 linked accounts", "A person should review the linked
    accounts" and "The customer has no linked accounts" pass, while "The customer has the linked
    accounts" and "The customer has 7 linked accounts" do not. Italian possessives count only
    before the accounts or as their predicate ("i suoi account", "sono suoi"; "il cluster ... i
    suoi legami" passes); "behind" only before the accounts, them or a cluster; "in the name" only
    with a possessive or "of".
  - Topics (V4, V17): keep, kept, remains, available, offered, paid, gone, still and "no longer"
    are topics only within three words of a lever noun (coupons, codes, shipping, refund,
    exchange, swaps, window, terms, perks...): "the cluster keeps its welcome coupons" is a
    topic, "the customer kept the S", "no photo is available" and "the item no longer fits" are
    not. "Tight" and "tough" are no longer restrictions (tighter, tightened, tougher are); the
    withdrawal path, the right of withdrawal, "later orders", "size guide", "shipped to", coupon
    use, codes used, an order with a coupon, "expensive" and a closed return window are
    observations; "crook" is a whole word ("a crooked seam" passes).
  - Levels (V5, V11): a negation counts only before the level word ("not high", "isn't high";
    not "high (0.87): an estimate, not a cause"; in its stretch of the clause since round 6);
    margin, profit and spend are whole words ("marginally" is not a margin); "over the period" is
    not "above"; a level word that sizes a contribution ("a significant contributor", "a small
    effect", "contributes little") is not a level of the score.
  - Grounding (V16, V17): after the ")" that closes a "(" opened right before the number, and
    right after a number that follows a metric name, a contribution verb from a short list is
    allowed ("returns_90d (4) contributes", "returns_90d = 4 raises the score"); "by" before a
    count after a participle of use ("used by 7 linked accounts"); "less" is a sign only right
    before a number; "payment" and "shared" may stand before a count noun.
  - Missing evidence (V14): `build_case` raises CaseError for an item that does not pass the
    statement checks (a number in digits, words or Roman numerals, a topic, a banned term), so
    the model can say what is missing, as the prompt asks: "too few orders observed", not "fewer
    than 3 orders observed". The pipeline phrases of the API branch ("Fewer than 4 order lines
    observed") must be reworded when the branches merge.
  - Agent (V12, V13, V18): a number in parentheses right after a word is the value of a label,
    not an accounting sign ("weight (0.98)"), and "meno" is a sign only right before a number;
    the records a clause names are ordered by where their names stand ("this case's cluster ...
    than the Rossi family"); in "7 vs 3" with one record in scope, the first number is that
    record's and the second the other record of its kind named in the sentence, or else the one
    other record of that kind read; a factor is named by its feature, "factor" may stand for any
    one factor, and "score" is not a kind word; the mock summary has no first person ("Other
    questions answered here: ...").
- **Retry feedback (V17).** The second call gets each code with where it was found and the words
  the validator matched in the model's own answer ("TERMS_TOPIC in operator_explanation[2]:
  keeps its welcome coupons"), never case text; a character outside the allowlist is written as
  its code point. The model can avoid the word instead of guessing.
- **Customer message (V7, V8, V9).** In Q3 and Q4, when the decision grants the free size
  exchange (scope all_orders, so it also holds for the current return), the current-return
  sentence says so: "This return keeps the terms of your order, and the size exchange is free."
  ("Questo reso mantiene le condizioni del tuo ordine, e il cambio taglia è gratuito."), and
  {terms} leaves it out, so it is never a next-order term caused by past returns, nor missing
  from this return. The cluster coupon block has no {terms} phrase when the base has no discount
  codes; a benefit alone now leaves no {terms} and the notice names the next orders. The
  restrictive sentence says "these return terms" ("queste condizioni"), not "new" ones: {terms}
  is computed against the merchant base, not the customer's own order. This changes the {terms}
  field (`llm/messages.term_phrases` and `available_placeholders` take the base), not the
  schemas; the golden example is unchanged, since the Navigli base has the free size exchange.
  To keep 120 words, the notice says "your previous returns" ("i tuoi resi precedenti") and the
  Italian {terms} sentence "prima dell'acquisto". The tests sweep the 48 bases, every Q3 and Q4
  scenario, both refund variants and three order snapshots (the base, a former Q4 snapshot, a
  former Q1 snapshot).
- **Profile guard (V6).** The guard test reads `settings.get_profile("ci")`, so the whole suite
  also runs under `HYPOTHESIS_PROFILE=explore`.

- **Round 6, the closing verification (W1 to W7).** The verifier found demo-path over-blocks
  on b4ee1e4. Every correct statement and answer it quoted is in the corpus first, with variants
  written apart; the correct statements that break a prompt rule (a negated level, a number in
  words) are a corpus list of their own that must regenerate, with the words in the feedback,
  and never go to the template.
  - Safety net (W2, W3): a risk or value level read from a statement's words is a heuristic, so
    a level that is not the engine's is TERMS_TOPIC (regenerate, with the words that name it:
    "risk score: not high"), never DECISION_ECHO. DECISION_ECHO stays for the `decision_echo`
    object and for an explicit quadrant label or Roman numeral other than the engine's ("Q4",
    "QIV", "quadrant 2", "the fourth quadrant"). The earlier probes that expected DECISION_ECHO
    for a level ("The risk score is low." in Q4, "Customer margin is substantial.") now expect
    TERMS_TOPIC.
  - Narrower levels (W2, W3): a comparative is not a level ("pushes the risk score higher", "a
    higher risk score"), unless it compares with a threshold ("higher than the threshold") or
    qualifies an axis ("a higher-risk customer"); after a verb that moves the score, a word that
    sizes the move is not a level ("lowers the risk score a little"), and neither are "a little"
    and "by a small margin". A negation counts before the level word in its comma-free stretch
    ("not in the high range", "does not push the risk score into the high range"), and "far
    from" and "nowhere near" anywhere before it.
  - Flag and caution (W5) are topics only about a person or the case ("flag this customer", "the
    case was flagged", "treat the linked accounts with caution", "proceed with caution"), never
    with the model as subject ("the model flags cluster_welcome_coupons (7)") or about reading
    the score ("read the risk score with caution"); "a red flag" is a topic.
  - Grounding words (W5, W6): more contribution verbs after "(N)" or a metric name (plays,
    outweighs, makes, pulls, moves...); conjunctions and modals after a number (despite,
    although, should, may...; "even" before though, if or when); "short" is a sign only next to
    a number ("42.1 EUR short", "short by 42.1"). With a welcome-coupon fact cited, "7 coupons" is
    that fact; "withdrawal period" is an observation; "belongs to" a cluster named in up to three
    words ("the Rossi family cluster") is not ownership.
  - Agent (W1, W4): in a clause, each "a vs b" pair (also "instead of", "rather than", "invece
    di", "anziché", with up to three field words between: "7 linked accounts vs 3") binds a to
    the first record of the comparison and b to the second: the two records of one kind the
    clause names, in order, the reference of the comparison ("compared with", "than", "rispetto
    a") last, or the one record named and the other record of its kind; such a clause is not
    split at "vs". A parenthesis that names a record ("(F-ROSSI: 3)") binds its numbers to it and
    leaves the scope of the sentence as it was. Ordinals second to fifth rank the factors or
    links named after them in their sentence, in tool order ("the second days_since_delivery =
    6"); any other ordinal is a number in words, so the answer falls back. The integer of an
    "id:value" lever string of get_case is a field of the case record ("a 60 day return window
    lever"), and the field "_days" matches a field with the word days (a factor's own feature).
  - Customer wording (W7): "these changes to the return terms" ("queste modifiche alle condizioni
    di reso") would make the longest Italian message 121 words before the clause (English 119),
    over the 120 of spec 8.7, so the restrictive sentence keeps "these return terms"; a test
    measures both.

The residual risk is accepted, and why. Precision first means some paraphrases pass: a proper
name or pronoun with a verb of use rather than control ("Marco uses the linked accounts"), a verb
of possession outside the list, a same-person claim in words the patterns do not have, a period
converted into words without a digit ("returned a week after delivery", when the fact is 6 days),
a rank or a decision in words not on the lists. This is acceptable because of where the text
goes. Operator statements and agent answers reach only the operator, never the customer; the
interface labels them as a Gemma draft and shows the engine decision, its levers and every cited
fact from code beside them; the agent has no write tool; the customer message is built from
reviewed sentences and code fields only; and every restrictive action (Q3, Q4) needs a person who
approves it, with a reason and a review date in the audit log (rule 5). A paraphrase that slips
through can mislead a reviewer for a moment; it cannot change a decision, a lever or a customer's
rights. A check that rejects correct output, instead, sends the case to the template and hides
the explanation the operator needs, on exactly the cases shown in the demo.

## Consequences

- `llm_case.json` is produced by `llm.case.build_case` for the golden Q4 decision, and a test
  rebuilds it. `llm_output.json` passes the validator.
- `infra/spikes/g1_guided_json.py` still loads `llm_output.schema.json`, so SPK-G1 tests the new
  shape. Its system text describes statements and sentence ids, and its user text asks for an
  explanation that cites fact ids and for sentence ids. SPK-G1 also measures whether xgrammar
  handles the 19-id enum without slowing down.
- The CI coverage gate should include the safety-critical LLM modules: `llm/text.py`,
  `llm/messages.py`, `llm/legal_clause.py`, `llm/decision.py` and `llm/templates.py`.
- After the adversarial review of 13 Oct, the validator is stricter (all in `llm/`, with
  regression tests):
  - terms and decisions written in words (lever words, who pays, full or partial refund,
    decision verbs) were first rejected in customer messages by a code TERMS_WORDING; since
    decision 8 the same check is part of the phrasebook gate, and the code is gone;
  - an operator statement that names a quadrant other than the engine's is a DECISION_ECHO
    (template); since decision 9 one that talks about levers is TERMS_TOPIC (regenerate). Only
    the engine's quadrant is stripped as an identifier;
  - number words also cover ordinals, zero, fractions, multiples and numbers glued to a unit;
    month, weekday and relative-day names and times of day outside greetings are units. Format
    characters are read both as removed and as spaces, and any dash joins a word;
  - "same person" also covers its synonyms: same, one or single with customer, user, owner and
    similar nouns, in English and Italian;
  - any dash or minus right before a digit is a sign. A scale suffix ("42.1k"), a percent sign
    or a sign word ("minus", "a loss of") next to a number makes it ungrounded (decision 9 turns
    this into an allowlist for statements);
  - `check_local_url` allows only 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12 and
    192.168.0.0/16, after unwrapping IPv4-mapped addresses (fc00::/7 dropped in round 2);
  - the agent refuses more write requests (turn off, Italian plural imperatives, participles
    such as "wants coupons disabled"). It falls back when an answer has a decision verb, advice,
    a same-person synonym or a number word. An integer in an answer must equal a tool value.
- Decision 8 changes the output contract: the validator codes LITERAL_NUMBER, LITERAL_UNIT,
  LEGAL_WORDING, TERMS_WORDING, PLACEHOLDER_SYNTAX, PLACEHOLDER_UNAVAILABLE, TERMS_MISSING and
  TOO_LONG are gone (no model text left for them); SENTENCE_ID and SCRIPT are new.
  `ExpectedDecision` gains `restrictive`, from `policy/legal_guard.is_restrictive`.
- Decision 9 adds the code TERMS_TOPIC and removes the `policy` fact entity (contract change).
- Spec 19.D shows the prompt of the phrasebook task, with the topic rule, and the prompt test
  compares it with `llm/prompts.SYSTEM_PROMPT` line for line again.
- Word counts: the message has at most 119 words before the clause, in Italian, for Q4 on a
  base without free size exchange and with a known refund (English: at most 116). This is what
  `tests/test_llm_review3.py` measures over 48 merchant bases (windows of 14, 30 and 45 days,
  every combination of the four boolean terms), every decision kind, with and without a known
  refund and every allowed selection (at most two extras in Q3 and Q4 since decision 10, which
  adds the sentence that this return keeps its terms, with the free size exchange since decision
  11). The limit of spec 8.7 holds, with one word of margin. The clause comes on top (the guarantee clause alone is about 75 words), so
  no claim is made about the length with the clause.
- Known limits:
  - the customer message is safe by construction only as far as the phrasebook is: a new
    sentence needs review, and the gate tests run the text checks on it. The model still
    chooses which allowed sentences to use, so a selection can be less apt for the case (for
    example a size sentence when size was not the reason), never wrong about the decision;
  - the operator statements and the agent answers are still free text: the banned-term,
    number-word, action and ownership checks there are blocklists, narrowed by the character
    allowlist, the topic check and grounding on cited facts in their context and on record
    fields. A paraphrase they miss reaches the operator, not the customer, and the operator
    approves every restrictive action;
  - the topic check still over-blocks some words: "window", "quick", "declining" or "accepted" in
    an observation make the statement regenerate (with the word in the feedback since decision
    11), then the template; so do "future", "priority", "money", "safe" or "reliable" about the
    customer, and "quadrant" without a label. Since decision 11, "available", "remains", "keeps",
    "offered" and "paid" count only next to a lever noun, and "shipped", "expensive" and "tight"
    not at all. The grounding context does too: a verb right after a number ("0.87 suggests") is
    not a function word, except a contribution verb after "(N)" or after a metric name, and a
    unit of another kind anywhere in the statement ("7 linked accounts, created on different
    days", citing only the cluster size) makes it ungrounded; numbers and ranks in words ("two
    sizes", "the second factor") are never grounded;
  - the agent's question check is a heuristic. A clitic glued to a noun-like imperative
    ("Segnale più forte?") is refused, and so is a write verb that opens a clause as an idiom
    ("Hold on, what is the deadline?"). A write verb after a subject is a statement ("What if you
    approve it?" is answered), and a request without a write verb ("Coupons off for R-MARCO,
    thanks") is not caught. A request still reaches only a read-only agent with no write tool;
  - the agent's answer check falls back on some plain answers: "I" or "we" with any past form but
    a read verb ("I noted" is a read, "I was told they were disabled" falls back), a write form
    later in a first-person sentence, "to be" with a participle, "done", a clause that opens with
    a write -ing form ("Disabling coupons is a lever of Q4"). Idioms with no write word ("Coupons
    are now history for R-MARCO") pass;
  - the field binding of the agent knows seven fields (the return window since round 6); another
    field written next to a number ("1.0 coupons per account") is grounded on any number of the
    records in scope, and so is a field word when no record read has that field ("7 linked
    accounts" copied from a score factor phrase). A clause that names one record and puts its
    value second ("2900 days against 9 days in R-MARCO") binds the first number to the record it
    names, so the answer falls back, and so does an answer that says "the cluster" after both
    clusters were read, without naming one, or an ordinal after the factor it ranks
    ("days_since_delivery (6), the second factor");
  - Roman numerals are detected only in upper case and with two or more letters; "XL" is left
    out as a size;
  - "sei" and "may" are left out on purpose (a verb and a modal). "day", "giornata" and
    "weekend" are allowed only after a greeting adjective ("buona giornata");
  - "uno", "one", "secondo" (also "according to"), "double", "half" and "quarter" are refused
    by the phrasebook gate, so no sentence uses them; "first" and "second" cause regenerations
    in operator statements;
  - a place in the matrix ("top left") is a topic, whatever the quadrant, since the case does
    not say how the matrix is drawn;
  - "un cliente" and "una persona" count as "same person" only in a sentence about accounts;
  - a sign given by other words ("the margin is below what it was") is not detected.
