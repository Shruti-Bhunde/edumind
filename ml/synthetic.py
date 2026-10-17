"""Simulated learners: a way to bootstrap labelled data before any real data exists.

IMPORTANT: this is *simulated*. A model trained on it learns the simulator's assumptions
(plus its noise), so its scores say nothing about real students. Use it to prove the
pipeline works, then retrain on real data (see ml/README.md for datasets).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from fractions import Fraction
from math import gcd

from app.fraction_engine import fmt, lcm
from app.misconceptions import SKILL_PATTERNS, predictions
from app.questions import BANK, Question, SKILLS

OTHER = "OTHER"


def _q(skill: str, kind: str, payload: dict, answer, input_type: str, choices=()) -> Question:
    return Question(id=f"syn-{skill}-{abs(hash(str(sorted(payload.items())))) % 10**8}", skill=skill,
                    kind=kind, payload=payload, prompt="", input_type=input_type, answer=answer,
                    answer_text=str(answer), difficulty=1, display={}, choices=tuple(choices))


def random_question(rng: random.Random, skill: str) -> Question:
    """A fresh question from a wider range than the shipped bank (so models must generalise)."""
    while True:
        if skill == "add":
            b, d = rng.randint(2, 12), rng.randint(2, 12)
            a, c = rng.randint(1, b - 1) if b > 1 else 1, rng.randint(1, d - 1) if d > 1 else 1
            if b == d or gcd(a, b) != 1 or gcd(c, d) != 1:
                continue
            payload = dict(a=a, b=b, c=c, d=d)
            q = _q("add", "add", payload, Fraction(a, b) + Fraction(c, d), "fraction")
        elif skill == "subtract":
            b, d = rng.randint(3, 12), rng.randint(2, 11)
            if d >= b:
                continue
            a, c = rng.randint(2, b - 1), rng.randint(1, d - 1)
            if gcd(a, b) != 1 or gcd(c, d) != 1 or Fraction(a, b) <= Fraction(c, d):
                continue
            payload = dict(a=a, b=b, c=c, d=d)
            q = _q("subtract", "subtract", payload, Fraction(a, b) - Fraction(c, d), "fraction")
        elif skill == "compare":
            x, y = rng.randint(2, 11), rng.randint(3, 14)
            n1, n2 = rng.randint(1, x - 1), rng.randint(1, y - 1)
            if x == y or Fraction(n1, x) == Fraction(n2, y):
                continue
            payload = dict(a=n1, b=x, c=n2, d=y)
            q = _q("compare", "compare", payload, max(Fraction(n1, x), Fraction(n2, y)), "choice",
                   ({"value": f"{n1}/{x}"}, {"value": f"{n2}/{y}"}))
        elif skill == "equivalent":
            if rng.random() < 0.5:
                b, k = rng.randint(2, 8), rng.randint(2, 5)
                a = rng.randint(1, b - 1)
                if gcd(a, b) != 1:
                    continue
                payload = dict(a=a, b=b, t=b * k)
                q = _q("equivalent", "blank", payload, a * k, "integer")
            else:
                b, k = rng.randint(2, 8), rng.randint(2, 5)
                a = rng.randint(1, b - 1)
                if gcd(a, b) != 1:
                    continue
                payload = dict(a=a, b=b, c=a * k, d=b * k)
                q = _q("equivalent", "yesno", payload, "yes", "choice",
                       ({"value": "yes"}, {"value": "no"}))
        else:  # name
            N = rng.randint(3, 12)
            k = rng.randint(1, N - 1)
            if gcd(N, k) != 1:
                continue
            q = _q("name", "name", dict(parts=N, shaded=k), Fraction(k, N), "fraction")
        if predictions(q.skill, q.kind, q.payload, q.answer):
            return q


def _as_text(value) -> str:
    if isinstance(value, Fraction):
        return fmt(value)
    return str(value)


def _typed(rng: random.Random, q: Question, pred) -> str:
    """How a simulated learner writes a predicted wrong answer.

    Choice questions must use the exact printed option. Otherwise ~60% write the natural,
    unsimplified form (e.g. 2/5) and the rest write it simplified, like real learners do.
    """
    if pred.typed is not None and (q.kind == "compare" or rng.random() < 0.6):
        return pred.typed
    return _as_text(pred.value)


def _slip(rng: random.Random, q: Question) -> str | None:
    """A careless wrong answer unrelated to any misconception (None if there isn't one)."""
    if q.kind == "yesno":
        return None  # the only wrong answer to these items is the M02 pattern itself
    for _ in range(50):
        if q.kind == "yesno":
            cand = "no"
        elif q.kind == "blank":
            cand = str(max(1, q.answer + rng.choice([-2, -1, 1, 2, 3])))
        elif q.kind == "compare":
            cand = rng.choice(q.choices)["value"]
        else:
            n, d = rng.randint(1, 15), rng.randint(2, 18)
            cand = f"{n}/{d}"
        preds = {_as_text(p.value) for p in predictions(q.skill, q.kind, q.payload, q.answer)}
        if q.kind == "compare":
            from app.fraction_engine import parse_fraction
            ok = parse_fraction(cand).value != q.answer
        else:
            ok = cand != _as_text(q.answer) and cand not in preds
        if ok:
            return cand
    return "1/2"


@dataclass(frozen=True)
class Example:
    question: Question
    submitted: str
    label: str  # a pattern id, or OTHER


def simulate(n: int, seed: int = 0, use_bank_fraction: float = 0.3) -> list[Example]:
    """n wrong-answer examples from simulated learners.

    Latent learner types: holds one misconception (applies it ~85% of the time),
    careless (random slip), or guessing. Right answers are discarded: the model is
    only ever asked about answers that were already marked wrong.
    """
    rng = random.Random(seed)
    skills = list(SKILLS)
    out: list[Example] = []
    while len(out) < n:
        skill = rng.choice(skills)
        if rng.random() < use_bank_fraction:
            q = rng.choice([b for b in BANK if b.skill == skill])
        else:
            q = random_question(rng, skill)
        preds = predictions(q.skill, q.kind, q.payload, q.answer)
        if not preds:
            continue
        roll = rng.random()
        pats = SKILL_PATTERNS[skill]
        if roll < 0.70:                      # holds a misconception
            pid = rng.choice(pats)
            mine = [p for p in preds if p.pattern_id == pid]
            if not mine:
                continue
            if rng.random() < 0.85:
                out.append(Example(q, _typed(rng, q, rng.choice(mine)), pid))
            else:                            # applied it inconsistently: slip
                ans = _slip(rng, q)
                if ans is not None:
                    out.append(Example(q, ans, OTHER))
        else:                                # careless or guessing
            ans = _slip(rng, q)
            if ans is not None:
                out.append(Example(q, ans, OTHER))
    return out
