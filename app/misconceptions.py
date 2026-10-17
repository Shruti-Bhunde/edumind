"""The misconception library (patterns M01-M06 from the project plan).

Each pattern can *predict* the specific wrong answer a learner holding that
misconception would give for a given question. Diagnosis compares the learner's
actual answer with those predictions; it never guesses from free text.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .fraction_engine import lcm


@dataclass(frozen=True)
class Pattern:
    id: str
    name: str
    summary: str
    skills: tuple[str, ...]


PATTERNS: dict[str, Pattern] = {
    "M01": Pattern(
        "M01",
        "Adding the bottom numbers directly",
        "Adds the numerators and the denominators separately, e.g. 1/2 + 1/3 = 2/5.",
        ("add",),
    ),
    "M02": Pattern(
        "M02",
        "Equivalent-fraction confusion",
        "Treats equivalent fractions as different amounts, or changes only the top "
        "(or adds the same number to the top and bottom).",
        ("equivalent",),
    ),
    "M03": Pattern(
        "M03",
        "Whole-number bias",
        "Believes a bigger bottom number means a bigger fraction, e.g. 1/8 > 1/6.",
        ("compare",),
    ),
    "M04": Pattern(
        "M04",
        "Top / bottom number role confusion",
        "Mixes up what the top and bottom numbers mean, or compares shaded with unshaded parts.",
        ("name",),
    ),
    "M05": Pattern(
        "M05",
        "Common-denominator slip",
        "Finds a common bottom number but changes only one of the top numbers.",
        ("add", "subtract"),
    ),
    "M06": Pattern(
        "M06",
        "Subtracting tops and bottoms directly",
        "Subtracts the numerators and the denominators separately.",
        ("subtract",),
    ),
}

SKILL_PATTERNS: dict[str, tuple[str, ...]] = {}
for _p in PATTERNS.values():
    for _s in _p.skills:
        SKILL_PATTERNS.setdefault(_s, tuple())
        SKILL_PATTERNS[_s] = tuple(sorted(SKILL_PATTERNS[_s] + (_p.id,)))


@dataclass(frozen=True)
class Prediction:
    pattern_id: str
    value: object  # same type as the normalised learner answer (Fraction / int / str)
    reason: str  # plain-language description of the faulty method
    typed: str | None = None  # how a learner would naturally write it (unsimplified); None = str(value)


def predictions(skill: str, kind: str, payload: dict, correct: object) -> list[Prediction]:
    """Wrong answers that each misconception would produce for this question.

    Predictions equal to the correct answer, or duplicating an earlier prediction,
    are dropped so that every remaining prediction is a discriminating signal.
    """
    out: list[Prediction] = []

    if skill in ("add", "subtract"):
        a, b, c, d = (payload[k] for k in "abcd")
        L = lcm(b, d)
        a2, c2 = a * (L // b), c * (L // d)
        if skill == "add":
            out.append(Prediction("M01", Fraction(a + c, b + d),
                                  "added the tops and the bottoms separately", f"{a + c}/{b + d}"))
            n1, n2 = a2 + c, a + c2
        else:
            if b > d and a > c:
                out.append(Prediction("M06", Fraction(a - c, b - d),
                                      "subtracted the tops and the bottoms separately",
                                      f"{a - c}/{b - d}"))
            n1, n2 = a2 - c, a - c2
        if n1 > 0:
            out.append(Prediction("M05", Fraction(n1, L),
                                  "changed only the first fraction to the common bottom number",
                                  f"{n1}/{L}"))
        if n2 > 0:
            out.append(Prediction("M05", Fraction(n2, L),
                                  "changed only the second fraction to the common bottom number",
                                  f"{n2}/{L}"))

    elif skill == "compare":
        a, b, c, d = (payload[k] for k in "abcd")
        if b != d:
            bigger_bottom = Fraction(a, b) if b > d else Fraction(c, d)
            shown = f"{a}/{b}" if b > d else f"{c}/{d}"  # exactly as printed on the choice
            out.append(Prediction("M03", bigger_bottom,
                                  "picked the fraction with the bigger bottom number", shown))

    elif skill == "equivalent":
        if kind == "yesno":
            if correct == "yes":
                out.append(Prediction("M02", "no",
                                      "treated different-looking fractions as different amounts"))
        else:
            a, b, t = payload["a"], payload["b"], payload["t"]
            out.append(Prediction("M02", a + (t - b),
                                  "added the same number to the top and the bottom"))
            out.append(Prediction("M02", a, "left the top number unchanged"))

    elif skill == "name":
        n, k = payload["parts"], payload["shaded"]
        out.append(Prediction("M04", Fraction(n, k), "put the shaded count on the bottom",
                              f"{n}/{k}"))
        out.append(Prediction("M04", Fraction(k, n - k),
                              "compared the shaded parts with the unshaded parts", f"{k}/{n - k}"))

    seen: list[object] = [correct]
    unique: list[Prediction] = []
    for p in out:
        if p.value in seen:
            continue
        seen.append(p.value)
        unique.append(p)
    return unique
