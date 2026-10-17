"""The reviewed question bank and deterministic answer checking.

The bank is generated from a fixed random seed, so question ids are stable
between runs. Every question is filtered so that the misconception library can
tell its predicted wrong answers apart from the correct answer.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from fractions import Fraction
from math import gcd

from .fraction_engine import AnswerParseError, fmt, lcm, parse_fraction, parse_int
from .misconceptions import predictions

BANK_VERSION = "1.0"

SKILLS: dict[str, dict[str, str]] = {
    "add": {"name": "Adding fractions", "blurb": "Add fractions that have different bottom numbers."},
    "subtract": {"name": "Subtracting fractions", "blurb": "Take one fraction away from another."},
    "compare": {"name": "Comparing fractions", "blurb": "Decide which of two fractions is larger."},
    "equivalent": {"name": "Equivalent fractions", "blurb": "Spot fractions that show the same amount."},
    "name": {"name": "Naming fractions", "blurb": "Read a fraction from a picture of a bar."},
}


@dataclass(frozen=True, eq=False)
class Question:
    id: str
    skill: str
    kind: str  # add | subtract | compare | yesno | blank | name
    payload: dict
    prompt: str
    input_type: str  # fraction | choice | integer
    answer: object  # Fraction | int | str
    answer_text: str
    difficulty: int
    display: dict
    choices: tuple[dict, ...] = field(default_factory=tuple)

    def public(self) -> dict:
        """What the browser may see. Never includes the answer or predicted errors."""
        return {
            "id": self.id,
            "skill": self.skill,
            "skill_name": SKILLS[self.skill]["name"],
            "kind": self.kind,
            "prompt": self.prompt,
            "input_type": self.input_type,
            "choices": [dict(c) for c in self.choices],
            "display": self.display,
            "difficulty": self.difficulty,
        }


@dataclass(frozen=True)
class CheckResult:
    correct: bool
    normalized: object
    note: str | None


def _expr(*parts: dict) -> dict:
    return {"type": "expr", "parts": list(parts)}


def _f(n: int, d: int) -> dict:
    return {"f": [n, d]}


def _build_bank() -> list[Question]:
    rng = random.Random(7)
    bank: list[Question] = []

    def add_q(**kw) -> None:
        bank.append(Question(**kw))

    # ---- add ---------------------------------------------------------------
    cands = []
    for b in range(2, 9):
        for d in range(2, 9):
            if b == d:
                continue
            for a in range(1, b):
                for c in range(1, d):
                    if gcd(a, b) != 1 or gcd(c, d) != 1 or lcm(b, d) > 24:
                        continue
                    total = Fraction(a, b) + Fraction(c, d)
                    if total >= 2:
                        continue
                    payload = dict(a=a, b=b, c=c, d=d)
                    ids = {p.pattern_id for p in predictions("add", "add", payload, total)}
                    if {"M01", "M05"} <= ids:
                        cands.append((payload, total))
    chosen = sorted(rng.sample(cands, 12), key=lambda x: lcm(x[0]["b"], x[0]["d"]))
    for i, (p, total) in enumerate(chosen, 1):
        add_q(id=f"add-{i:02d}", skill="add", kind="add", payload=p,
              prompt=f"Work out {p['a']}/{p['b']} + {p['c']}/{p['d']}. Give your answer as a fraction.",
              input_type="fraction", answer=total, answer_text=fmt(total),
              difficulty=1 if lcm(p["b"], p["d"]) <= 12 else 2,
              display=_expr(_f(p["a"], p["b"]), {"op": "+"}, _f(p["c"], p["d"])))

    # ---- subtract ----------------------------------------------------------
    cands = []
    for b in range(3, 10):
        for d in range(2, b):
            for a in range(2, b):
                for c in range(1, d):
                    if gcd(a, b) != 1 or gcd(c, d) != 1 or lcm(b, d) > 24:
                        continue
                    if not Fraction(a, b) > Fraction(c, d) or not a > c:
                        continue
                    payload = dict(a=a, b=b, c=c, d=d)
                    diff = Fraction(a, b) - Fraction(c, d)
                    ids = {p.pattern_id for p in predictions("subtract", "subtract", payload, diff)}
                    if {"M06", "M05"} <= ids:
                        cands.append((payload, diff))
    chosen = sorted(rng.sample(cands, 10), key=lambda x: lcm(x[0]["b"], x[0]["d"]))
    for i, (p, diff) in enumerate(chosen, 1):
        add_q(id=f"subtract-{i:02d}", skill="subtract", kind="subtract", payload=p,
              prompt=f"Work out {p['a']}/{p['b']} − {p['c']}/{p['d']}. Give your answer as a fraction.",
              input_type="fraction", answer=diff, answer_text=fmt(diff),
              difficulty=1 if lcm(p["b"], p["d"]) <= 12 else 2,
              display=_expr(_f(p["a"], p["b"]), {"op": "−"}, _f(p["c"], p["d"])))

    # ---- compare -----------------------------------------------------------
    same_num, diff_num = [], []
    for n in range(1, 5):
        for x in range(n + 1, 11):
            for y in range(x + 1, 13):
                same_num.append((n, x, n, y))
    for x in range(3, 10):
        for y in range(x + 1, 13):
            for n1 in range(1, x):
                for n2 in range(2, y):
                    if n2 > n1 and gcd(n1, x) == 1 and gcd(n2, y) == 1 \
                            and Fraction(n1, x) > Fraction(n2, y):
                        diff_num.append((n1, x, n2, y))
    picks = rng.sample(same_num, 6) + rng.sample(diff_num, 4)
    rng.shuffle(picks)
    for i, (a, b, c, d) in enumerate(picks, 1):
        if rng.random() < 0.5:  # vary which side holds the larger fraction
            a, b, c, d = c, d, a, b
        left, right = Fraction(a, b), Fraction(c, d)
        bigger = max(left, right)
        shown = f"{a}/{b}" if left > right else f"{c}/{d}"  # as printed on the choice button
        add_q(id=f"compare-{i:02d}", skill="compare", kind="compare",
              payload=dict(a=a, b=b, c=c, d=d),
              prompt=f"Which fraction is larger: {a}/{b} or {c}/{d}?",
              input_type="choice", answer=bigger, answer_text=shown,
              difficulty=1 if a == c else 2,
              display=_expr(_f(a, b), {"op": "?"}, _f(c, d)),
              choices=({"value": f"{a}/{b}", "label": f"{a}/{b}", "frac": [a, b]},
                       {"value": f"{c}/{d}", "label": f"{c}/{d}", "frac": [c, d]}))

    # ---- equivalent --------------------------------------------------------
    yes_pairs = [(a, b, a * k, b * k) for b in range(2, 7) for a in range(1, b)
                 for k in (2, 3, 4) if gcd(a, b) == 1]
    no_pairs = [(2, 3, 3, 4), (1, 2, 2, 5), (3, 5, 4, 7)]
    picks = [(p, "yes") for p in rng.sample(yes_pairs, 5)] + [(p, "no") for p in no_pairs]
    rng.shuffle(picks)
    n = 0
    for (a, b, c, d), ans in picks:
        n += 1
        add_q(id=f"equivalent-{n:02d}", skill="equivalent", kind="yesno",
              payload=dict(a=a, b=b, c=c, d=d),
              prompt=f"Are {a}/{b} and {c}/{d} equal? Choose Yes or No.",
              input_type="choice", answer=ans, answer_text=ans.capitalize(), difficulty=1,
              display=_expr(_f(a, b), {"op": "="}, _f(c, d)),
              choices=({"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}))
    blanks = [(a, b, b * k) for b in range(2, 7) for a in range(1, b)
              for k in (2, 3, 4) if gcd(a, b) == 1]
    for a, b, t in rng.sample(blanks, 5):
        n += 1
        ans = a * (t // b)
        add_q(id=f"equivalent-{n:02d}", skill="equivalent", kind="blank",
              payload=dict(a=a, b=b, t=t),
              prompt=f"{a}/{b} = ?/{t}. What number goes in the box?",
              input_type="integer", answer=ans, answer_text=str(ans), difficulty=2,
              display=_expr(_f(a, b), {"op": "="}, {"blank": True, "over": t}))

    # ---- name --------------------------------------------------------------
    cands = [(N, k) for N in range(3, 11) for k in range(1, N) if gcd(N, k) == 1]
    for i, (N, k) in enumerate(rng.sample(cands, 8), 1):
        ans = Fraction(k, N)
        add_q(id=f"name-{i:02d}", skill="name", kind="name",
              payload=dict(parts=N, shaded=k),
              prompt=f"The bar is split into {N} equal parts and {k} {'is' if k == 1 else 'are'} shaded. "
                     f"What fraction of the bar is shaded?",
              input_type="fraction", answer=ans, answer_text=fmt(ans), difficulty=1,
              display={"type": "bar", "parts": N, "shaded": k})

    return bank


BANK: list[Question] = _build_bank()
BY_ID: dict[str, Question] = {q.id: q for q in BANK}


def by_skill(skill: str) -> list[Question]:
    return [q for q in BANK if q.skill == skill]


def get_question(question_id: str) -> Question | None:
    return BY_ID.get(question_id)


def check_answer(q: Question, raw: str) -> CheckResult:
    """Deterministically mark an answer. Raises AnswerParseError for unreadable input."""
    note = None
    if q.input_type == "fraction":
        parsed = parse_fraction(raw)
        normalized: object = parsed.value
        correct = normalized == q.answer
        if correct and not parsed.in_simplest_form:
            note = f"That's right. You can also write it as {fmt(parsed.value)}."
    elif q.input_type == "integer":
        normalized = parse_int(raw)
        correct = normalized == q.answer
    else:  # choice
        value = (raw or "").strip().lower() if q.kind == "yesno" else (raw or "").strip()
        if value not in {c["value"] for c in q.choices}:
            raise AnswerParseError("Please choose one of the options.")
        normalized = parse_fraction(value).value if q.kind == "compare" else value
        correct = normalized == q.answer
    return CheckResult(correct, normalized, note)
