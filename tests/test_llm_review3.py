"""Regression probes of the adversarial re-review of 13 Oct, round 3 (findings T1 to T16).

Every probe quoted in the review is a parametrized test here, and each one passed the checks of
commit 0db8c75. The over-blocking examples of T8 and T16 are must-accept tests: 0db8c75 rejected
them. The file uses only interfaces that exist at 0db8c75 (validator codes are compared as
strings), so it can be run against that commit to see every test fail there.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from llm.agent import MockAgent, VllmAgent, is_write_request
from llm.decision import ExpectedDecision, serialize_decision
from llm.messages import render, template_fields
from llm.phrasebook import (
    MAX_EXTRAS,
    SLOTS,
    MessageContext,
    allowed_sentences,
    compose,
    context_of,
    mandatory_body,
    render_selection,
    selection_problems,
)
from llm.templates import default_selection, template_output
from llm.text import LANGS, Lang, word_count
from llm.validator import ValidationResult, validate
from policy.engine import decide
from policy.types import Decision, DiminishedValueClaim, ReturnTerms
from tests.factories import BASE, THRESHOLDS, TODAY
from tests.llm_factories import ROOT, SCENARIOS, Built, make
from tests.test_llm_agent import FakeReader, ScriptedChat, answer, calls, tool_call

GOLDEN = make("Q4_cluster")
REFUND = Decimal("59.9")
CLAIM = Decimal("12.5")  # a diminished-value claim (E17), other than the refund it leaves
# The two merchant bases of T1: the only lever of Q3 (and of Q4 on the second one) is the notice.
NO_INSTANT_REFUND = ReturnTerms(30, True, False, True, True)
LEGAL_MINIMUM = ReturnTerms(14, False, False, True, False)
# The sweep of T1 and T5: three windows and every combination of the four boolean terms.
SWEEP_BASES = [
    ReturnTerms(window, *flags)
    for window in (14, 30, 45)
    for flags in itertools.product((True, False), repeat=4)
]


def decided(
    name: str, base: ReturnTerms, refund: Decimal | None = None, claim: Decimal | None = None
) -> tuple[Decision, ExpectedDecision]:
    """The engine decision of a scenario on another merchant base (the order bought under it),
    with a diminished-value claim of `claim` when given (the {deduction} sentence, round 4 of the
    API review, finding E17)."""
    scenario = SCENARIOS[name]
    req = replace(scenario.request, order_terms=base)
    decision = decide(req, scenario.scores, base, THRESHOLDS, TODAY)
    if claim is not None:
        withheld = DiminishedValueClaim(claim, ("PH-1",), confirmed_by="ahmed", rule_id="DV-1")
        decision = replace(decision, diminished_value_claim=withheld)
    _, expected = serialize_decision(decision, base, refund)
    return decision, expected


def check(built: Built, text: str, metrics: tuple[str, ...] = (), label: str = "OBSERVED") -> Any:
    """The template output plus one statement that cites the facts of these metrics."""
    ids = {f["metric"]: f["id"] for f in built.case["facts"]}
    out = template_output(built.case, built.expected)
    out["operator_explanation"].append(
        {"label": label, "fact_ids": [ids[m] for m in metrics], "text": text}
    )
    return validate(out, built.expected, built.case)


def ask_vllm(final: str, question: str = "Tell me about the cluster") -> str:
    chat = ScriptedChat(
        [
            calls(tool_call("get_case")),
            calls(tool_call("get_cluster", {"cluster_id": "R-MARCO"})),
            answer(final),
        ]
    )
    return VllmAgent(chat, FakeReader()).ask("RET-1", question).source


def assert_rejected(result: ValidationResult, *codes: str) -> None:
    assert not result.ok
    assert set(codes) & set(result.codes), result.errors


# ---- T1: the transparency notice refers to future orders ---------------------------------------


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("refund", [None, REFUND])
@pytest.mark.parametrize(
    "name, base",
    [("Q3", NO_INSTANT_REFUND), ("Q3_cluster", NO_INSTANT_REFUND), ("Q4", LEGAL_MINIMUM)],
)
def test_t1_a_notice_alone_never_reads_as_about_the_current_refund(
    name: str, base: ReturnTerms, refund: Decimal | None, lang: Lang
) -> None:
    decision, expected = decided(name, base, refund)
    ctx = context_of(expected)
    assert "transparency_notice:true" in ctx.lever_ids and "terms" not in ctx.placeholders
    final = render_selection(default_selection(ctx), decision, base, lang, refund)
    assert "These terms" not in final and "Queste condizioni" not in final
    future = {
        "it": "Le condizioni di reso dei tuoi prossimi ordini tengono conto dei tuoi resi",
        "en": "The return terms of your next orders take into account your previous returns",
    }
    assert future[lang] in final


@pytest.mark.parametrize("lang", LANGS)
def test_t1_the_notice_follows_the_terms_or_names_the_next_orders_on_every_base(
    lang: Lang,
) -> None:
    for base, name, refund in itertools.product(SWEEP_BASES, sorted(SCENARIOS), (None, REFUND)):
        ctx = context_of(decided(name, base, refund)[1])
        body = mandatory_body(ctx, lang)
        for before, sentence in zip(body, body[1:], strict=False):
            if sentence.startswith(("These terms", "Queste condizioni")):
                assert "{terms}" in before, (base, name, sentence)


# ---- T2, T3: the refund date is a limit; insufficient evidence states the standard terms -------


@pytest.mark.parametrize("refund", [None, REFUND])
def test_t2_both_languages_give_the_refund_date_as_a_limit(refund: Decimal | None) -> None:
    built = make("Q2", refund_eur=refund)
    ctx = context_of(built.expected)
    # {deadline} carries its Italian article ("il 28 ottobre 2026", "l'11 ottobre 2026"; round 4
    # of the API review, finding E22)
    assert "è previsto al più tardi per {deadline}." in mandatory_body(ctx, "it")[0]
    assert "is due by {deadline}." in mandatory_body(ctx, "en")[0]
    final = render_selection(["OPEN_THANKS", "CLOSE_REGARDS"], built.decision, BASE, "it", refund)
    assert "è previsto al più tardi per il 28 ottobre 2026." in final


@pytest.mark.parametrize(
    "lang, text",
    [
        # round 4, finding U3: "have", not "keep", which claims unchanged terms
        ("en", "Your next orders have our standard return terms."),
        ("it", "I tuoi prossimi ordini hanno le nostre condizioni di reso standard."),
    ],
)
def test_t3_insufficient_evidence_states_the_standard_terms(lang: Lang, text: str) -> None:
    scenario = SCENARIOS["insufficient"]
    req = replace(scenario.request, order_terms=LEGAL_MINIMUM)  # a restricted snapshot
    decision = decide(req, scenario.scores, BASE, THRESHOLDS, TODAY)
    final = render_selection(["OPEN_THANKS", "CLOSE_REGARDS"], decision, BASE, lang)
    assert text in final
    assert "same as always" not in final and "quelle di sempre" not in final
    assert not {"keep", "keeps", "mantengono"} & set(re.findall(r"\w+", final.lower()))


# ---- T4: incoherent selections ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, ids",
    [
        ("Q4_cluster", ["OPEN_SORRY_FIT", "HELP_SIZE", "CONTACT_REPLY", "INFO_TERMS", "CLOSE_DAY"]),
        ("guarantee", ["OPEN_SORRY_ITEM", "GUARANTEE_PHOTO", "CONTACT_REPLY", "CLOSE_DAY"]),
        ("Q4_cluster", ["OPEN_THANKS", "HELP_SIZE", "CONTACT_REPLY", "CLOSE_REGARDS"]),
        ("Q4_cluster", ["OPEN_SORRY_FIT", "CLOSE_DAY"]),
        ("guarantee", ["OPEN_SORRY_ITEM", "CLOSE_DAY"]),
    ],
)
def test_t4_incoherent_selections_are_refused(name: str, ids: list[str]) -> None:
    built = make(name)
    out = template_output(built.case, built.expected)
    out["customer_message"]["sentence_ids"] = ids
    assert_rejected(validate(out, built.expected, built.case), "SENTENCE_ID")


# ---- T5: the word counts the ADR states are the ones the tests measure --------------------------


def unordered_selections(ctx: MessageContext) -> list[list[str]]:
    by_slot = {s: [x.id for x in allowed_sentences(ctx) if x.slot == s] for s in SLOTS}
    return [
        [opening, *extras, closing]
        for opening in by_slot["opening"]
        for count in range(MAX_EXTRAS + 1)
        for extras in itertools.combinations(by_slot["extra"], count)
        for closing in by_slot["closing"]
    ]


def message_words(text: str) -> int:
    return word_count(text.split("\n\n")[0])


def test_t5_the_adr_states_the_measured_word_counts() -> None:
    """The longest message before the clause over the sweep bases, every decision kind, with and
    without a known refund, with a deduction (E17) and every allowed selection: counted sentence
    by sentence (the order of the extras does not change it), then the longest one is rendered
    to check the count."""
    best, longest = 0, None
    seen: set[MessageContext] = set()
    amounts = ((None, None), (REFUND, None), (REFUND, CLAIM))
    for base, name, (refund, claim) in itertools.product(SWEEP_BASES, sorted(SCENARIOS), amounts):
        decision, expected = decided(name, base, refund, claim)
        ctx = context_of(expected)
        if ctx in seen:
            continue
        seen.add(ctx)
        coherent = [ids for ids in unordered_selections(ctx) if not selection_problems(ids, ctx)]
        for lang in LANGS:
            filled = " ".join(mandatory_body(ctx, lang))
            for field, value in template_fields(decision, base, lang, refund).items():
                filled = filled.replace("{" + field + "}", value)
            words = {s.id: word_count(s.text(lang)) for s in allowed_sentences(ctx)}
            for ids in coherent:
                count = word_count(filled) + sum(words[i] for i in ids)
                if count > best:
                    best, longest = count, (decision, base, refund, ctx, lang, ids)
    assert longest is not None and best <= 120
    decision, base, refund, ctx, lang, ids = longest
    assert message_words(render(compose(ids, ctx, lang), decision, base, lang, refund)) == best
    adr = (ROOT / "docs/adr/ADR-14-llm-contracts-v2.md").read_text(encoding="utf-8")
    assert f"at most {best} words before the clause" in adr
    assert "within 120 words with the clause" not in adr


# ---- T6: ownership and agency over the linked accounts (rule 11) -------------------------------

T6_STATEMENTS = [
    "The customer owns the linked accounts.",
    "The linked accounts are owned by the customer.",
    "The linked accounts belong to the customer.",
    "The customer controls the linked accounts.",
    "The customer operates the linked accounts.",
    "The customer runs the linked accounts.",
    "The customer manages the linked accounts.",
    "The customer created the linked accounts.",
    "The linked accounts share an owner.",
    "The linked accounts are the same actor.",
    "The linked accounts are the same operator.",
    "The linked accounts are the one and only customer.",
    "The linked accounts are the same and only customer.",
    "Il cliente possiede gli account collegati.",
    "Il cliente controlla gli account collegati.",
    "Il cliente gestisce gli account collegati.",
    "Gli account collegati sono riconducibili al cliente.",
    "Gli account collegati fanno capo al cliente.",
    "Gli account collegati sono suoi.",
    "Gli account collegati sono del cliente.",
]


@pytest.mark.parametrize("text", T6_STATEMENTS)
def test_t6_ownership_of_the_linked_accounts_is_a_banned_term(text: str) -> None:
    assert_rejected(check(GOLDEN, text, label="HYPOTHESIS"), "BANNED_TERM")


@pytest.mark.parametrize(
    "final",
    [
        "The customer controls the 7 linked accounts of cluster R-MARCO.",
        "The 7 linked accounts of cluster R-MARCO belong to the customer.",
    ],
)
def test_t6_ownership_in_an_agent_answer_falls_back(final: str) -> None:
    assert ask_vllm(final) == "fallback"


# ---- T7: operator statements do not talk about levers, terms, restrictions or levels ----------

T7_Q4_PROBES = [
    "Return shipping is free.",
    "Spedizione del reso gratuita.",
    "Refunds are instant.",
    "Rimborso immediato attivo.",
    "Promo codes are enabled.",
    "Vouchers are enabled.",
    "Coupons for the cluster are enabled.",
    "Instant refund for future orders is enabled.",
    "Instant refund, not disabled.",
    "Neither instant refund nor coupons are disabled.",
    "There is no restriction on instant refund.",
    "It is not true that instant refund is off.",
    "Instant refund is offered.",
    "Instant refund is unchanged.",
    "Instant refund still applies.",
    "The customer is still eligible for instant refunds.",
    "Coupons can still be redeemed.",
    "The return window is a month.",
    "The return window is unchanged.",
    "Risk level is low.",
    "Il rischio è molto basso.",
    "The risk is very high and the value is quite high.",
    "Risk is below the threshold.",
    "The case sits in Q-one.",
]
T7_UNRESTRICTED_PROBES = [
    "Restrictive levers apply to future orders and not to the current return.",
    "Restrictive levers apply with no exceptions.",
    "Stricter return terms apply to this customer.",
    "Si applicano limitazioni ai prossimi ordini.",
    "The customer loses instant refund.",
    "Instant refund is withheld.",
    "Return shipping is charged to the customer.",
    "Return window: XIV days.",
    "Risk level: high.",
    "The risk is very high.",
    "The value is very low.",
]


@pytest.mark.parametrize("text", T7_Q4_PROBES)
def test_t7_levers_terms_and_levels_in_a_statement_are_rejected(text: str) -> None:
    assert_rejected(check(GOLDEN, text), "TERMS_TOPIC", "DECISION_ECHO")


@pytest.mark.parametrize("name", ["guarantee", "insufficient", "Q1"])
@pytest.mark.parametrize("text", T7_UNRESTRICTED_PROBES)
def test_t7_restrictions_and_levels_without_restrictive_levers(name: str, text: str) -> None:
    assert_rejected(check(make(name), text), "TERMS_TOPIC", "DECISION_ECHO")


# ---- T8: what the validator must accept, and lever sentences that are now regenerated ----------

T8_MUST_ACCEPT: list[tuple[str, str, tuple[str, ...], str]] = [
    ("Q4_cluster", "The risk score is high (0.87).", ("risk_score",), "SCORE"),
    (
        "Q4_cluster",
        "The largest contribution to the risk score (0.87) is cluster_welcome_coupons: 7.",
        ("risk_score", "cluster_welcome_coupons"),
        "SCORE",
    ),
    ("Q4_cluster", "Customer net margin — 42.1 EUR — is low.", ("value_eur_12m",), "OBSERVED"),
    (
        "Q4_cluster",
        "Cluster R-MARCO — 7 linked accounts sharing a device, an address and a card.",
        ("cluster_size",),
        "OBSERVED",
    ),
    ("Q4_cluster", "Linked accounts in the cluster - 7.", ("cluster_size",), "OBSERVED"),
    ("Q4_cluster", "This is a high-value order.", (), "OBSERVED"),
    ("Q4_cluster", "The risk score value is high.", ("risk_score",), "SCORE"),
    ("Q3", "The returned order has a low value.", (), "OBSERVED"),
    (
        "Q4_negative_margin",
        "Customer net margin is negative: -42.1 EUR.",
        ("value_eur_12m",),
        "OBSERVED",
    ),
]
T8_LEVER_SENTENCES = [
    "The return window is reduced to 14 days for future orders.",
    "The return window is shortened to 14 days.",
    "Coupons cannot be used on future orders.",
    "Instant refund is not offered on future orders.",
    "Instant refund is off; no other lever changes.",
    "Instant refund is disabled. No change to past orders.",
    "Coupons are disabled, not removed from past orders.",
    "Instant refund on future orders is disabled.",
    "Coupons on future orders are disabled.",
]


@pytest.mark.parametrize("name, text, metrics, label", T8_MUST_ACCEPT)
def test_t8_grounded_statements_about_scores_counts_and_data_pass(
    name: str, text: str, metrics: tuple[str, ...], label: str
) -> None:
    result = check(make(name), text, metrics, label)
    assert result.ok, result.errors


@pytest.mark.parametrize("text", T8_LEVER_SENTENCES)
def test_t8_lever_sentences_are_regenerated_not_sent_to_the_template(text: str) -> None:
    result = check(GOLDEN, text)
    assert "TERMS_TOPIC" in result.codes and "DECISION_ECHO" not in result.codes, result.errors
    assert result.action is not None and result.action.value == "REGENERATE"


# ---- T9: a character allowlist on the raw text ---------------------------------------------------

T9_STATEMENTS = [
    "This is 🅵🆁🅰🆄🅳.",
    "This is 🅕🅡🅐🅤🅓.",
    "This is 🇫🇷🇦🇺🇩.",
    "This is ⒡raud.",
    "They are the same pers°n.",
    "They are the same pers○n.",
    "They are the same pers●n.",
    "They are the same pers◯n.",
    "They are the same pers⊙n.",
    "This is a sc∀m.",
    "This is a sc∧m.",
]


@pytest.mark.parametrize("text", T9_STATEMENTS)
def test_t9_symbols_that_look_like_letters_are_regenerated(text: str) -> None:
    assert_rejected(check(GOLDEN, text, label="HYPOTHESIS"), "SCRIPT")


@pytest.mark.parametrize(
    "final",
    [
        "The cluster R-MARCO has 7 linked accounts; this looks like 🅵🆁🅰🆄🅳.",
        "The cluster R-MARCO has 7 linked accounts of the same pers°n.",
    ],
)
def test_t9_symbols_in_an_agent_answer_fall_back(final: str) -> None:
    assert ask_vllm(final) == "fallback"


@pytest.mark.parametrize(
    "text, metrics",
    [
        ("Customer net margin — 42.1 EUR, over the period.", ("value_eur_12m",)),
        ("Customer net margin: 42.1 €.", ("value_eur_12m",)),
        ("The customer’s text says “it does not fit”…", ()),
        ("Days from delivery to the return notice – 6.", ("days_since_delivery",)),
    ],
)
def test_t9_typographic_characters_of_the_allowlist_pass(text: str, metrics: tuple[str]) -> None:
    result = check(GOLDEN, text, metrics)
    assert result.ok, result.errors


# ---- T10: numbers in words, Roman numerals and foreign cardinals --------------------------------

T10_STATEMENTS = [
    "Returns rose sevenfold.",
    "Returns rose tenfold.",
    "Returns rose ninefold.",
    "Returns quadrupled.",
    "Returns halved.",
    "Returns are septuple the average.",
    "Returns: tenish.",
    "Returns: fiveish.",
    "Returns: twelvish.",
    "Linked accounts: sevens.",
    "Returns: a couple.",
    "Returns: a pair.",
    "Returns: a handful.",
    "Returns: a trio.",
    "Margin: a grand.",
    "Margin: a quid.",
    "Margin: a buck.",
    "Returns: VII.",
    "Margin: XLII EUR.",
    "Returns: tres.",
    "Returns: drei.",
    "Returns: cinq.",
    "Returns: sieben.",
    "Returns: sept.",
    "Returns: siete.",
]


@pytest.mark.parametrize("text", T10_STATEMENTS)
def test_t10_numbers_in_other_forms_are_ungrounded(text: str) -> None:
    assert_rejected(check(GOLDEN, text, label="HYPOTHESIS"), "UNGROUNDED_NUMBER")


@pytest.mark.parametrize("name", ["Q1", "guarantee"])
def test_t10_a_roman_window_is_rejected_without_restrictive_levers(name: str) -> None:
    assert_rejected(check(make(name), "Return window: XIV days."), "UNGROUNDED_NUMBER")


# ---- T11: the unit, scale, rate and sign around a grounded number -------------------------------

T11_MARGIN = [
    "Customer net margin: 42.1 USD.",
    "Customer net margin: $42.1.",
    "Customer net margin: £42.1.",
    "Customer net margin: 42.1 GBP.",
    "Customer net margin: 42.1 cents.",
    "Customer net margin: 42.1 %.",
    "Customer net margin: 42,1 %.",
    "Customer net margin: 42.1 ‰.",
    "Customer net margin: 42.1 pct.",
    "Customer net margin: 42.1 percentage points.",
    "Customer net margin: 42.1 bln EUR.",
    "Customer net margin: 42.1 trillion EUR.",
    "Customer net margin: 42.1 thou EUR.",
    "Customer net margin: 42.1 kilo EUR.",
    "Customer net margin: 42.1 times the median.",
    "Customer net margin: 42.1 x the median.",
    "Customer net margin: 42.1 EUR per month.",
    "Customer net margin: 42.1 EUR per day.",
    "Customer net margin: 42.1 EUR weekly.",
    "Customer net margin: 42.1 EUR per order.",
    "Customer net margin: 42.1 EUR each.",
    "Customer net margin: losing 42.1 EUR.",
    "We lose 42.1 EUR on this customer.",
    "The customer costs us 42.1 EUR.",
    "Customer net margin: short by 42.1 EUR.",
    "Customer net margin: 42.1 EUR, in arrears.",
    "Customer net margin: 42.1 EUR net cost.",
    "Customer net margin: money-losing, 42.1 EUR.",
    "Customer net margin: 42.1 EUR in passivo.",
    "Customer net margin: 42.1 EUR in rimessa.",
    "Customer net margin: less 42.1 EUR.",
    "Customer net margin: 42.1 EUR deducted.",
    "Customer net margin: 42.1 EUR written off.",
    "Customer net margin: under 42.1 EUR.",
    "Customer net margin: <42.1 EUR.",
]


@pytest.mark.parametrize("text", T11_MARGIN)
def test_t11_a_modified_margin_is_not_the_cited_fact(text: str) -> None:
    assert_rejected(check(GOLDEN, text, ("value_eur_12m",)), "UNGROUNDED_NUMBER")


@pytest.mark.parametrize(
    "text",
    [
        "Days from delivery to the return notice: 6 weeks.",
        "The item was returned 6 months after delivery.",
    ],
)
def test_t11_a_swapped_time_unit_is_not_the_cited_fact(text: str) -> None:
    assert_rejected(check(GOLDEN, text, ("days_since_delivery",)), "UNGROUNDED_NUMBER")


# ---- T12, T15: write requests hidden in a sentence, Italian forms, dotted spellings -------------

T12_REQUESTS = [
    # a question word earlier in the same sentence
    "Tell me what this case is and approve it.",
    "What is this case, then approve it.",
    "What is this case - approve it.",
    "What is this case: approve it",
    "Explain why the case is Q4 and disable the coupons.",
    "Check who owns R-MARCO, then disable the coupons.",
    "Dimmi perché è Q4 e disattiva i coupon.",
    "Spiegami chi possiede R-MARCO e blocca gli account.",
    # Italian enclitic imperatives
    "Approvalo.",
    "Disattivali.",
    "Bloccali tutti",
    "Annullalo",
    "Rimborsalo",
    "Toglili a R-MARCO",
    "Approviamolo",
    "Disattiviamoli",
    # formal Lei imperatives
    "Approvi il reso, per cortesia.",
    "Disattivi i coupon di R-MARCO.",
    "Annulli l'ordine.",
    "Tolga i coupon.",
    # T15: dotted spellings
    "ap.prove it",
    "dis.able the coupons",
    "Please dis.able the coupons",
    # verbs that were missing
    "Freeze the R-MARCO accounts",
    "Lock the account",
    "Restrict the account",
    "Lift the restrictions",
    "Process the return",
    "Authorize the return",
    "Autorizza il reso",
    "Emetti il rimborso",
    "Riduciamo la finestra di reso",
    "Whitelist R-MARCO",
    "Drop the coupons for R-MARCO",
]


@pytest.mark.parametrize("question", T12_REQUESTS)
def test_t12_write_requests_are_refused_before_any_read(question: str) -> None:
    assert is_write_request(question)
    reader, chat = FakeReader(), ScriptedChat([answer("Done.")])
    result = VllmAgent(chat, reader).ask("RET-1", question)
    assert result.refused and reader.log == [] and chat.requests == []


@pytest.mark.parametrize(
    "question, final",
    [
        (
            "Tell me what this case is and turn off the coupons for R-MARCO.",
            "I have already turned off the coupons for R-MARCO.",
        ),
        (
            "Dimmi chi possiede R-MARCO e disattiva i coupon.",
            "Ho già disattivato i coupon per R-MARCO.",
        ),
        ("Disattivali per R-MARCO.", "Abbiamo provveduto a disattivare i coupon per R-MARCO."),
        ("Freeze the R-MARCO accounts.", "I went ahead and froze the R-MARCO accounts."),
    ],
)
def test_t12_the_end_to_end_pairs_are_refused_and_their_answers_fall_back(
    question: str, final: str
) -> None:
    reader = FakeReader()
    chat = ScriptedChat(
        [
            calls(tool_call("get_case")),
            calls(tool_call("get_cluster", {"cluster_id": "R-MARCO"})),
            answer(final),
        ]
    )
    result = VllmAgent(chat, reader).ask("RET-1", question)
    assert result.refused and reader.log == []
    assert ask_vllm(final) == "fallback"  # the answer alone, after a plain question


def test_t12_the_mock_agent_refuses_an_enclitic_imperative() -> None:
    assert MockAgent(FakeReader()).ask("RET-1", "Disattivali per R-MARCO.").source == "refusal"


# ---- T13, T15: actions claimed, ordered or required in an answer --------------------------------

T13_ANSWERS = [
    # completion claims
    "I have already disabled the coupons for R-MARCO.",
    "I've just disabled the coupons for R-MARCO.",
    "I have just disabled the coupons for R-MARCO.",
    "We have successfully disabled the coupons for R-MARCO.",
    "I went ahead and disabled the coupons for R-MARCO.",
    "The coupons for R-MARCO have now been disabled.",
    "As requested, coupons for R-MARCO are disabled.",
    "Your request was carried out: coupons for R-MARCO are off.",
    "Ho appena disattivato i coupon per R-MARCO.",
    "Ho già disattivato i coupon per R-MARCO.",
    "Li ho appena disattivati.",
    "Abbiamo già tolto i coupon a R-MARCO.",
    "Abbiamo provveduto a disattivare i coupon per R-MARCO.",
    "I coupon di R-MARCO sono stati disattivati come richiesto.",
    # obligations
    "Coupons for R-MARCO are to be disabled.",
    "Coupons for R-MARCO need disabling.",
    "Coupons for R-MARCO require disabling.",
    "Make sure coupons for R-MARCO are off.",
    "Ensure coupons for R-MARCO are off.",
    "I coupon di R-MARCO sono da disattivare.",
    "I coupon di R-MARCO andrebbero disattivati.",
    "Conviene disattivare i coupon di R-MARCO.",
    "Si disattivino i coupon di R-MARCO.",
    "Ricordati di spegnere i coupon di R-MARCO.",
    "Assicurati che i coupon di R-MARCO siano spenti.",
    # imperatives
    "Freeze the R-MARCO accounts.",
    "Lock the R-MARCO accounts.",
    "Restrict the R-MARCO accounts.",
    "Lift the restrictions on R-MARCO.",
    "Process the refund now.",
    "Release the refund.",
    "Drop the coupons for R-MARCO.",
    "Disattivali per R-MARCO.",
    "Disattivi i coupon di R-MARCO.",
    "Dis.able coupons for R-MARCO.",
]


@pytest.mark.parametrize("final", T13_ANSWERS)
def test_t13_answers_that_act_or_ask_to_act_fall_back(final: str) -> None:
    assert ask_vllm(final) == "fallback"


# ---- T14: numbers bound to the record field their words name ------------------------------------

T14_ANSWERS = [
    "The cluster R-MARCO has 9 linked accounts.",
    "Il cluster R-MARCO ha 9 account collegati.",
    "The cluster R-MARCO has 7 linked accounts using 9 distinct cards.",
    "The cluster has 9 linked accounts.",
    "Accounts linked: 9.",
    "The disputed link has weight 0.6 and 4 shared events.",
    "Between C-MARCO-1 and C-MARCO-3 there are 4 shared events.",
    "Link L-102 has weight 0.6. It has 4 shared events.",
]


@pytest.mark.parametrize("final", T14_ANSWERS)
def test_t14_a_number_must_be_the_field_its_words_name(final: str) -> None:
    assert ask_vllm(final) == "fallback"


# ---- T16: plain read questions that mention a write-verb noun -----------------------------------

T16_QUESTIONS = [
    "Tell me about the refund.",
    "Show me the refund deadline.",
    "Explain the refund for this order.",
    "Refund status?",
    "Summarize the case and the refund amount.",
    "Tell me about the flag on this case.",
    "Show the blocked accounts.",
    "List the merge history of R-MARCO.",
    "Which evidence supports the link? Also the refund date.",
]


@pytest.mark.parametrize("question", T16_QUESTIONS)
def test_t16_read_questions_are_answered(question: str) -> None:
    assert not is_write_request(question)


def test_t16_the_mock_agent_answers_a_question_about_the_refund() -> None:
    assert MockAgent(FakeReader()).ask("RET-1", "Tell me about the refund.").source == "mock"


def test_review3_probe_lists_have_no_repeats() -> None:
    lists = [T6_STATEMENTS, T7_Q4_PROBES, T9_STATEMENTS, T10_STATEMENTS, T11_MARGIN, T12_REQUESTS]
    lists += [T13_ANSWERS, T14_ANSWERS, T16_QUESTIONS, T8_LEVER_SENTENCES]
    for probes in lists:
        assert len(set(probes)) == len(probes)
    assert all(re.search(r"\S", p) for probes in lists for p in probes)
