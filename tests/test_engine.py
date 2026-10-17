"""Unit tests: fraction maths, question bank integrity, and the diagnostic engine."""
from fractions import Fraction

import pytest

from app.diagnostic import (MAX_FOLLOWUPS, apply_followup, choose_followup, decide,
                            followups_for, initial_scores)
from app.fraction_engine import AnswerParseError, fmt, parse_fraction, parse_int
from app.misconceptions import PATTERNS, SKILL_PATTERNS, predictions
from app.questions import BANK, BY_ID, SKILLS, by_skill, check_answer


# ----------------------------------------------------------------- fraction maths
@pytest.mark.parametrize("text,value", [
    ("3/4", Fraction(3, 4)), (" 3 / 4 ", Fraction(3, 4)), ("2", Fraction(2)),
    ("1 1/2", Fraction(3, 2)), ("6/8", Fraction(3, 4)), ("0/5", Fraction(0)),
    ("3⁄4", Fraction(3, 4)),
])
def test_parse_fraction_ok(text, value):
    assert parse_fraction(text).value == value


@pytest.mark.parametrize("text", ["", "abc", "1/0", "1.5", "-1/2", "1/2/3", "1 1/0", "99999/1"])
def test_parse_fraction_rejects(text):
    with pytest.raises(AnswerParseError):
        parse_fraction(text)


def test_parse_keeps_unsimplified_form():
    p = parse_fraction("6/8")
    assert (p.numerator, p.denominator, p.in_simplest_form) == (6, 8, False)


def test_parse_int_and_fmt():
    assert parse_int(" 12 ") == 12
    with pytest.raises(AnswerParseError):
        parse_int("1/2")
    assert fmt(Fraction(4, 2)) == "2" and fmt(Fraction(5, 6)) == "5/6"


# ------------------------------------------------------------------- question bank
def test_bank_shape():
    assert len({q.id for q in BANK}) == len(BANK)
    for skill in SKILLS:
        assert len(by_skill(skill)) >= 8, skill


@pytest.mark.parametrize("q", BANK, ids=lambda q: q.id)
def test_every_question_marks_its_own_answer_correct(q):
    res = check_answer(q, q.answer_text if q.input_type != "choice" else q.answer_text.lower()
                       if q.kind == "yesno" else q.answer_text)
    assert res.correct, q.id


@pytest.mark.parametrize("q", BANK, ids=lambda q: q.id)
def test_predictions_are_distinct_and_wrong(q):
    preds = predictions(q.skill, q.kind, q.payload, q.answer)
    # The only questions without a predicted wrong answer are "No" answers to a yes/no
    # equivalence check: a wrong "Yes" there isn't one of our misconception patterns.
    assert preds or (q.kind == "yesno" and q.answer == "no"), f"{q.id} has no diagnosable wrong answers"
    values = [p.value for p in preds]
    assert q.answer not in values
    assert len(values) == len(set(values))
    assert all(p.pattern_id in SKILL_PATTERNS[q.skill] for p in preds)


def test_public_question_never_leaks_answer():
    for q in BANK:
        blob = str(q.public())
        assert "answer" not in q.public()
        assert q.answer_text not in q.public().get("choices", []), q.id
        assert "payload" not in blob


def test_fraction_answers_accept_equivalent_forms():
    q = next(x for x in by_skill("add") if x.answer.denominator % 2 == 0)
    unsimplified = f"{q.answer.numerator * 2}/{q.answer.denominator * 2}"
    res = check_answer(q, unsimplified)
    assert res.correct and "also write it as" in res.note


def test_choice_questions_reject_unknown_options():
    q = by_skill("compare")[0]
    with pytest.raises(AnswerParseError):
        check_answer(q, "7/13")


# --------------------------------------------------------------- diagnostic engine
def _support_option(followup, pattern_id):
    """The option that most strongly supports a pattern (what a consistent learner would pick)."""
    return max(followup.options, key=lambda o: o.weights.get(pattern_id, 0))


@pytest.mark.parametrize("q", BANK, ids=lambda q: q.id)
def test_consistent_learner_gets_targeted_diagnosis(q):
    """For every question and every predicted wrong answer, a learner whose follow-up
    answer agrees with the misconception is diagnosed with exactly that pattern."""
    for pred in predictions(q.skill, q.kind, q.payload, q.answer):
        scores, evidence = initial_scores(q, pred.value)
        assert scores[pred.pattern_id] == 3
        f = choose_followup(q, scores, [])
        apply_followup(scores, evidence, f, _support_option(f, pred.pattern_id))
        d = decide(scores, 1)
        assert (d.status, d.pattern_id) == ("targeted", pred.pattern_id), (q.id, pred, scores)


def test_contradicting_evidence_leads_to_abstention():
    q = by_skill("add")[0]
    pred = next(p for p in predictions("add", "add", q.payload, q.answer) if p.pattern_id == "M01")
    scores, evidence = initial_scores(q, pred.value)
    f1 = choose_followup(q, scores, [])
    apply_followup(scores, evidence, f1, next(o for o in f1.options if o.id == "common"))
    assert decide(scores, 1).status == "ask_more"
    f2 = choose_followup(q, scores, [f1.id])
    apply_followup(scores, evidence, f2, next(o for o in f2.options if o.id == "mult"))
    d = decide(scores, 2)
    assert d.status == "general" and d.pattern_id is None


def test_guessing_never_produces_a_confident_label():
    for q in BANK:
        for pred in predictions(q.skill, q.kind, q.payload, q.answer):
            scores, evidence = initial_scores(q, pred.value)
            asked = []
            while True:
                f = choose_followup(q, scores, asked)
                if f is None:
                    break
                apply_followup(scores, evidence, f, next(o for o in f.options if o.id == "guess"))
                asked.append(f.id)
                d = decide(scores, len(asked))
                assert d.status != "targeted", (q.id, scores)
                if d.status == "general":
                    break
            assert len(asked) <= MAX_FOLLOWUPS


def test_unrecognised_answer_needs_two_agreeing_followups():
    q = by_skill("add")[0]
    scores, evidence = initial_scores(q, Fraction(999, 1000))
    assert all(v == 0 for v in scores.values())
    f1 = choose_followup(q, scores, [])
    apply_followup(scores, evidence, f1, next(o for o in f1.options if o.id == "sum"))
    assert decide(scores, 1).status == "ask_more"
    f2 = choose_followup(q, scores, [f1.id])
    assert "M01" in f2.targets or "M05" in f2.targets


def test_every_skill_has_two_followups_and_valid_weights():
    for q in {x.skill: x for x in BANK}.values():
        fs = followups_for(q)
        assert len(fs) >= MAX_FOLLOWUPS
        for f in fs:
            assert len({o.id for o in f.options}) == len(f.options)
            for o in f.options:
                assert set(o.weights) <= set(PATTERNS) | {"*"}
