# ADR-14: LLM contracts v2

Status: accepted, 13 Oct 2026; amended the same day after the second round of the adversarial
review (decision 8, the customer phrasebook). Changes `contracts/json/llm_case.schema.json`,
`contracts/json/llm_output.schema.json`, their examples, `llm/` and spec sections 8.7, 19.B, 19.C
and 19.D. Resolves review findings LLM-1 to LLM-14 of 12 Oct (US-304, US-305) and findings R1 to
R8 of the second adversarial review of 13 Oct.

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
| entity | customer, return, order, cluster or policy |
| metric | snake_case name: risk_score, value_eur_12m, a score factor feature, cluster_size, days_since_delivery, return_window_days |
| value | a JSON number, rounded half-up to 2 decimals |
| period | for example "as of 2026-10-14", "12 months to 2026-10-14", "2026-10-08/2026-10-14" |
| nature | real, synthetic or simulated (rule 11) |

These are the only numbers the explanation may use. The customer text, the policy text and the
identifiers never feed grounding.

### 2. Statements that cite facts (llm_output)

`operator_explanation` becomes an array of statements, each `{label, fact_ids, text}`:

- `label` is OBSERVED, SCORE or HYPOTHESIS;
- every number in `text` must equal the value of one of the facts it cites (decimal comma
  accepted, no rounding, no conversion, no number words);
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
| REGENERATE (then template) | every other code: SCHEMA, UNKNOWN_FACT, MISSING_FACT, LABEL_MISMATCH, UNGROUNDED_NUMBER, BANNED_TERM, SCRIPT, SENTENCE_ID, WRONG_LANGUAGE |

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
  terms to talk about). The sentences are warm and neutral (spec 16), with no number, amount,
  deadline, term, legal wording or decision word.
- `customer_message` is `{"sentence_ids": [...]}`, 2 to 5 ids from a closed enum: one opening, at
  most three extras, one closing.
- Code writes the message per language: opening, the mandatory body, the extras, the closing,
  then the legal clause (`render`). The body is built from the engine decision and never chosen
  by the model: the registration with {deadline} (and {amount} when known), the {terms} sentence
  when future terms change, the unchanged-terms sentence for insufficient evidence, the
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
  decision, with and without a known refund: the required clause is there once, amounts, terms
  and deadlines are the template fields, the language is right and the message stays within
  120 words before the clause.

What Gemma still writes: the operator statements (`operator_explanation`) and the answers of the
investigator agent (`llm/agent.py`). Both keep the text checks, hardened in the same round:

- R2: a lever value is read through negations ("coupons are not disabled" claims
  coupons_enabled:true), contradictions ("false? No, true") and Italian or camelCase names; a
  value that cannot be read is "?" and never matches. Q(I-IV) is a quadrant label. A quadrant
  described by its levels ("high-risk, low-value") must be the engine's. Without restrictive
  levers, "restrictive" and places in the matrix ("top left") are refused.
- R3: more same-person synonyms (consumer, purchaser, identity, household with a qualifier...),
  up to two words between qualifier and noun, and ownership of all the accounts ("the customer
  owns all the linked accounts", "sono tutti tuoi").
- R4: the agent checks each sentence of a question, with digits read as letters and dashes
  joined, and more verbs (accept, blacklist, Italian first person plural...). An answer with an
  imperative, an obligation ("must be disabled") or a claim of an action ("Done", "now off")
  falls back. A number in a clause that names a record (a link, a cluster, an order, the case)
  must be a value of that record.
- R5: a script allowlist. After NFKC, any letter outside a-z and the Italian accented vowels, or
  a combining mark left over, is a new code SCRIPT (regenerate) in a statement and a fallback in
  an answer.
- R6: a short cardinal glued to an abbreviated unit ("tenhrs", "dueh") and slang amounts
  ("tenner", "sennight") are numbers; abbreviations, holidays, periods and relative days are
  units in the phrasebook gate.
- R7: a sign after spaces ("- 42.1"), parentheses ("(42.1)"), plus-minus, a trailing dash, more
  scale and unit words ("grand", "kEUR", "percent", "bps") and sign words ("owed", "debt",
  "shortfall") make a number ungrounded.
- R8: `check_local_url` drops fc00::/7, because the compose network is IPv4 only; this refuses
  the EC2 IPv6 metadata endpoint fd00:ec2::254.

## Consequences

- `llm_case.json` is produced by `llm.case.build_case` for the golden Q4 decision, and a test
  rebuilds it. `llm_output.json` passes the validator.
- `infra/spikes/g1_guided_json.py` still loads `llm_output.schema.json`, so SPK-G1 tests the new
  shape. Its system text describes statements and sentence ids; its user text still mentions the
  {terms} placeholder, which guided decoding makes irrelevant. SPK-G1 also measures whether
  xgrammar handles the 19-id enum without slowing down.
- The CI coverage gate should include the safety-critical LLM modules: `llm/text.py`,
  `llm/messages.py`, `llm/legal_clause.py`, `llm/decision.py` and `llm/templates.py`.
- After the adversarial review of 13 Oct, the validator is stricter (all in `llm/`, with
  regression tests):
  - terms and decisions written in words (lever words, who pays, full or partial refund,
    decision verbs) were first rejected in customer messages by a code TERMS_WORDING; since
    decision 8 the same check is part of the phrasebook gate, and the code is gone;
  - an operator statement that names a quadrant or a lever value other than the engine's is a
    DECISION_ECHO (template). Only the engine's quadrant is stripped as an identifier;
  - number words also cover ordinals, zero, fractions, multiples and numbers glued to a unit;
    month, weekday and relative-day names and times of day outside greetings are units. Format
    characters are read both as removed and as spaces, and any dash joins a word;
  - "same person" also covers its synonyms: same, one or single with customer, user, owner and
    similar nouns, in English and Italian;
  - any dash or minus right before a digit is a sign. A scale suffix ("42.1k"), a percent sign
    or a sign word ("minus", "a loss of") next to a number makes it ungrounded;
  - `check_local_url` allows only 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12 and
    192.168.0.0/16, after unwrapping IPv4-mapped addresses (fc00::/7 dropped in round 2);
  - the agent refuses more write requests (turn off, Italian plural imperatives, participles
    such as "wants coupons disabled"). It falls back when an answer has a decision verb, advice,
    a same-person synonym or a number word. An integer in an answer must equal a tool value.
- Decision 8 changes the output contract: the validator codes LITERAL_NUMBER, LITERAL_UNIT,
  LEGAL_WORDING, TERMS_WORDING, PLACEHOLDER_SYNTAX, PLACEHOLDER_UNAVAILABLE, TERMS_MISSING and
  TOO_LONG are gone (no model text left for them); SENTENCE_ID and SCRIPT are new.
  `ExpectedDecision` gains `restrictive`, from `policy/legal_guard.is_restrictive`.
- Spec 19.D still shows the v2 prompt with task 3 as free prose. Its update is outside this
  change; until then the prompt test compares every other line with 19.D and checks that only
  the customer-message lines differ.
- Known limits:
  - the customer message is safe by construction only as far as the phrasebook is: a new
    sentence needs review, and the gate tests run the text checks on it. The model still
    chooses which allowed sentences to use, so a selection can be less apt for the case (for
    example a size sentence when size was not the reason), never wrong about the decision;
  - the operator statements and the agent answers are still free text: the banned-term,
    number-word, lever-name, action and ownership checks there are blocklists, narrowed by the
    script allowlist and by grounding on cited facts and named records. A paraphrase they miss
    reaches the operator, not the customer, and the operator approves every restrictive action;
  - the agent's action check refuses some state answers that start with a verb-like noun
    ("Refund deadline: ..."); they fall back to the list of evidence consulted;
  - Roman numerals written with ASCII letters are not detected;
  - "sei" and "may" are left out on purpose (a verb and a modal). "day", "giornata" and
    "weekend" are allowed only after a greeting adjective ("buona giornata");
  - "uno", "one", "secondo" (also "according to"), "double", "half" and "quarter" are refused
    by the phrasebook gate, so no sentence uses them; "first" and "second" cause regenerations
    in operator statements;
  - the 120-word limit of spec 8.7 holds for the message before the fixed legal clause (at most
    116 words today); the guarantee clause alone is about 75 words. The default selections stay
    within 120 words with the clause;
  - a quadrant described by one of the matrix places ("top left") is refused only without
    restrictive levers, since the case does not say how the matrix is drawn;
  - "un cliente" and "una persona" count as "same person" only in a sentence about accounts;
  - a sign given by other words ("the margin is below what it was") is not detected.
