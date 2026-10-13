# ADR-14: LLM contracts v2

Status: accepted, 13 Oct 2026; amended the same day after the second round of the adversarial
review (decision 8, the customer phrasebook) and after the third (decision 9, operator statements
without levers). Changes `contracts/json/llm_case.schema.json`,
`contracts/json/llm_output.schema.json`, their examples, `llm/` and spec sections 8.7, 8.8, 19.B,
19.C and 19.D. Resolves review findings LLM-1 to LLM-14 of 12 Oct (US-304, US-305), findings R1 to
R8 of the second adversarial review of 13 Oct and findings T1 to T16 of the third.

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
  score is low" in Q4); the level of the risk score, of an order or of an item is not a
  quadrant level ("The risk score is high (0.87)", "This is a high-value order" pass). The
  templates no longer name levers or terms.
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
  "Your next orders keep our standard return terms", not unchanged terms that code cannot know.
  A selection has at most one sentence that asks for a reply (HELP_SIZE, CONTACT_REPLY,
  INFO_TERMS, GUARANTEE_PHOTO) and CLOSE_DAY ("Thanks again") only after a thanks opening; the
  prompt lists these rules with the sentences. The word counts below are the ones the tests
  measure.

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
- Word counts: the message has at most 120 words before the clause, in Italian, for Q4 on a
  base without free size exchange and with a known refund (English: at most 116). This is what
  `tests/test_llm_review3.py` measures over 48 merchant bases (windows of 14, 30 and 45 days,
  every combination of the four boolean terms), every decision kind, with and without a known
  refund and every allowed selection. The limit of spec 8.7 holds, with no margin left for
  longer sentences. The clause comes on top (the guarantee clause alone is about 75 words), so
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
  - the topic check over-blocks by design: "window", "quick", "shipped", "expensive", "declining"
    or "accepted" in an observation make the statement regenerate, then the template. The
    grounding context does too: a verb right after a number ("0.87 suggests") is not a function
    word;
  - the agent's question check is a heuristic. Italian nouns equal to a formal imperative at the
    start of a clause ("Rimborsi in ritardo?", "Segnale più forte?" with a clitic) are refused;
    a proclitic before a formal imperative ("Lo approvi") and a request without a write verb
    ("Coupons off for R-MARCO, thanks") are not caught; a write verb after "the", "a" or a
    preposition is read as a noun, so "No approve it" (no comma) passes. A request still reaches
    only a read-only agent with no write tool;
  - the agent's answer check falls back on some state answers: "I" or "we" with any write form
    later in the sentence ("We see that the coupons were blocked" passes, "I was told they were
    disabled" does not), "to be" with a participle, "done";
  - the field binding of the agent knows six field words; another field written next to a
    number ("1.0 coupons per account") is grounded on any number of the records in scope, and
    so is a field word when no record read has that field ("7 linked accounts" copied from a
    score factor phrase). A clause that names one record and compares it with another ("2900
    days against 9 days in R-MARCO") is scoped to the record it names, so the answer falls back;
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
