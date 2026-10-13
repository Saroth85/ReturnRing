# ADR-14: LLM contracts v2

Status: accepted, 13 Oct 2026. Changes `contracts/json/llm_case.schema.json`,
`contracts/json/llm_output.schema.json`, their examples, `llm/` and spec sections 8.7, 19.B, 19.C
and 19.D. Resolves review findings LLM-1 to LLM-14 of 12 Oct (US-304, US-305).

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
- customer messages of at most 900 characters.

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
| REGENERATE (then template) | every other code: SCHEMA, grounding, banned terms, literal numbers, units, legal wording, placeholders, language, length |

A missing or altered legal clause is checked on the rendered message by
`llm/legal_clause.assert_clause`, which raises `LegalClauseError` (blocking).

### 7. Guarantee clause (LLM-11)

The v1 guarantee clause listed refund as a free choice next to repair and replacement. The
proposed draft follows Directive (EU) 2019/771, art. 13 and 14:

- repair or replacement first, free of charge, within a reasonable time;
- price reduction or termination where the law provides for it;
- never termination for a minor defect.

It is appended always on the guarantee path. It stays a DRAFT pending D-14. It is in spec 19.C,
and the LEG-06 test compares the code with it.

## Consequences

- `llm_case.json` is produced by `llm.case.build_case` for the golden Q4 decision, and a test
  rebuilds it. `llm_output.json` passes the validator.
- `infra/spikes/g1_guided_json.py` still loads `llm_output.schema.json`, so SPK-G1 tests the new
  shape. Its prompt still describes `operator_explanation` as a string, and should be updated
  when infra changes next.
- The CI coverage gate should include the safety-critical LLM modules: `llm/text.py`,
  `llm/messages.py`, `llm/legal_clause.py`, `llm/decision.py` and `llm/templates.py`.
- After the adversarial review of 13 Oct, the validator is stricter (all in `llm/`, with
  regression tests):
  - a new code TERMS_WORDING (regenerate) rejects customer messages that state terms or the
    decision in words: lever words (free, instant, shipping, coupons, exchange, window...), who
    pays, full or partial refund, and decision verbs (approve, reject, block, investigate...);
  - an operator statement that names a quadrant or a lever value other than the engine's is a
    DECISION_ECHO (template). Only the engine's quadrant is stripped as an identifier;
  - number words also cover ordinals, zero, fractions, multiples and numbers glued to a unit;
    month, weekday and relative-day names and times of day outside greetings are units. Format
    characters are read both as removed and as spaces, and any dash joins a word;
  - "same person" also covers its synonyms: same, one or single with customer, user, owner and
    similar nouns, in English and Italian;
  - any dash or minus right before a digit is a sign. A scale suffix ("42.1k"), a percent sign
    or a sign word ("minus", "a loss of") next to a number makes it ungrounded;
  - `check_local_url` allows only 127.0.0.0/8, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
    and fc00::/7, after unwrapping IPv4-mapped addresses;
  - the agent refuses more write requests (turn off, Italian plural imperatives, participles
    such as "wants coupons disabled"). It falls back when an answer has a decision verb, advice,
    a same-person synonym or a number word. An integer in an answer must equal a tool value.
- Known limits:
  - the banned-term, terms, number-word and lever-name checks are blocklists;
  - Roman numerals written with ASCII letters are not detected;
  - "sei" and "may" are left out on purpose (a verb and a modal). "day", "giornata" and
    "weekend" are allowed only after a greeting adjective ("buona giornata");
  - "uno" and "one" are rejected in customer messages and cause regenerations when used as
    articles or pronouns; so do "secondo" (also "according to"), "double", "half" and "quarter"
    in product names, and "first" and "second" in operator statements;
  - "un cliente" and "una persona" count as "same person" only in a sentence about accounts;
  - a sign given by other words ("the margin is below what it was") is not detected.
