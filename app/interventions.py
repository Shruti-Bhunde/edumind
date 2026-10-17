"""Reviewed, targeted explanations for each misconception, plus a general fallback.

Content is deliberately written in tentative language ("this may be what
happened") because a diagnosis is a hypothesis. Worked examples never reuse the
learner's own question, so the answer is not handed over.
"""
from __future__ import annotations

from fractions import Fraction

from .fraction_engine import fmt, lcm
from .misconceptions import PATTERNS
from .questions import Question


def _bar(label: str, parts: int, shaded: int, caption: str = "") -> dict:
    return {"label": label, "parts": parts, "shaded": shaded, "caption": caption}


def _key(q: Question):
    p = q.payload
    if q.skill in ("add", "subtract", "compare"):
        return frozenset({(p["a"], p["b"]), (p["c"], p["d"])})
    if q.skill == "equivalent":
        return (p["a"], p["b"])
    return (p.get("parts"), p.get("shaded"))


ADD_EXAMPLES = [(1, 2, 1, 3), (1, 4, 1, 2), (2, 3, 1, 6), (1, 3, 1, 4)]
SUB_EXAMPLES = [(3, 4, 1, 3), (1, 2, 1, 3), (2, 3, 1, 2), (3, 4, 1, 2)]
EQ_EXAMPLES = [(1, 2, 3), (2, 3, 2), (3, 4, 2), (1, 4, 3)]
CMP_EXAMPLES = [(1, 3, 1, 5), (2, 5, 2, 9), (1, 4, 1, 10), (3, 4, 3, 8)]
NAME_EXAMPLES = [(5, 2), (8, 3), (6, 1), (7, 4)]


def _common_denominator_example(q: Question, op: str) -> tuple[list[str], list[dict]]:
    examples = ADD_EXAMPLES if op == "+" else SUB_EXAMPLES
    chosen = next((e for e in examples
                   if frozenset({(e[0], e[1]), (e[2], e[3])}) != _key(q)), examples[0])
    a, b, c, d = chosen
    L = lcm(b, d)
    a2, c2 = a * (L // b), c * (L // d)
    n = a2 + c2 if op == "+" else a2 - c2
    result = Fraction(n, L)
    sym = "+" if op == "+" else "−"
    last = f"{a2}/{L} {sym} {c2}/{L} = {n}/{L}"
    if fmt(result) != f"{n}/{L}":
        last += f" = {fmt(result)}"
    lines = [
        f"Example: {a}/{b} {sym} {c}/{d}",
        f"{b} and {d} both go into {L}, so use {L} as the common bottom number.",
        f"{a}/{b} = {a2}/{L}  (top and bottom both × {L // b})",
        f"{c}/{d} = {c2}/{L}  (top and bottom both × {L // d})",
        last,
    ]
    bars = [
        _bar(f"{a}/{b}", b, a), _bar(f"{c}/{d}", d, c),
        _bar(f"{a2}/{L}", L, a2, f"same amount as {a}/{b}"),
        _bar(f"{c2}/{L}", L, c2, f"same amount as {c}/{d}"),
        _bar(f"{n}/{L}", L, n, "the answer"),
    ]
    return lines, bars


def _m01(q: Question) -> dict:
    lines, bars = _common_denominator_example(q, "+")
    return {
        "title": "Adding fractions: the pieces must be the same size first",
        "why": "This may be what happened: the bottom numbers were added together. "
               "A bottom number says how big the pieces are, so halves and thirds can't be "
               "counted together until both are cut into pieces of the same size.",
        "steps": [
            "Find a number that both bottom numbers go into (a common bottom number).",
            "Rewrite each fraction with that bottom number: multiply the top and the bottom by the same number.",
            "Now the pieces are the same size, so add only the top numbers and keep the bottom number.",
        ],
        "example_lines": lines, "bars": bars,
        "hint": "Make the bottom numbers the same first, then add only the top numbers.",
    }


def _m05(q: Question) -> dict:
    op = "+" if q.skill == "add" else "-"
    lines, bars = _common_denominator_example(q, op)
    return {
        "title": "Changing a fraction: whatever you do to the bottom, do to the top",
        "why": "This may be what happened: a common bottom number was found, but only one "
               "of the top numbers was changed to match. Changing just the bottom changes the amount.",
        "steps": [
            "Find the common bottom number.",
            "For EACH fraction, multiply the top and the bottom by the same number.",
            "Check both fractions now have the common bottom number and a matching top number.",
            "Then add or subtract the top numbers only.",
        ],
        "example_lines": lines, "bars": bars,
        "hint": "Both fractions need a new top number, not just a new bottom number.",
    }


def _m06(q: Question) -> dict:
    lines, bars = _common_denominator_example(q, "-")
    return {
        "title": "Subtracting fractions: same-size pieces first",
        "why": "This may be what happened: the top numbers and the bottom numbers were each "
               "subtracted separately. The bottom number describes the size of the pieces, "
               "so it should stay the same when we take pieces away.",
        "steps": [
            "Rewrite both fractions with the same bottom number.",
            "Subtract only the top numbers.",
            "Keep the common bottom number as it is.",
        ],
        "example_lines": lines, "bars": bars,
        "hint": "Make the bottom numbers the same, then subtract only the top numbers.",
    }


def _m02(q: Question) -> dict:
    e = next((x for x in EQ_EXAMPLES if (x[0], x[1]) != _key(q)), EQ_EXAMPLES[0])
    a, b, k = e
    return {
        "title": "Equivalent fractions: same amount, different-sized pieces",
        "why": "This may be what happened: a fraction was changed by adding to it, or "
               "different-looking fractions were treated as different amounts. Cutting a whole "
               "into more pieces and taking proportionally more pieces keeps the amount the same.",
        "steps": [
            "To make an equivalent fraction, multiply (or divide) the top AND the bottom by the same number.",
            "Never add the same number to the top and the bottom. That changes the amount.",
            "Check: shade both bars. If the shaded length matches, the fractions are equal.",
        ],
        "example_lines": [
            f"Example: {a}/{b}",
            f"Multiply the top and the bottom by {k}: {a}×{k} / {b}×{k} = {a * k}/{b * k}",
            f"{a}/{b} and {a * k}/{b * k} show exactly the same amount.",
        ],
        "bars": [_bar(f"{a}/{b}", b, a), _bar(f"{a * k}/{b * k}", b * k, a * k, "same amount")],
        "hint": "Multiply the top and the bottom by the same number. Don't add.",
    }


def _m03(q: Question) -> dict:
    e = next((x for x in CMP_EXAMPLES
              if frozenset({(x[0], x[1]), (x[2], x[3])}) != _key(q)), CMP_EXAMPLES[0])
    a, b, c, d = e
    bigger = (a, b) if Fraction(a, b) > Fraction(c, d) else (c, d)
    return {
        "title": "Comparing fractions: more pieces means smaller pieces",
        "why": "This may be what happened: the bigger bottom number was treated as the bigger "
               "fraction. But a bigger bottom number means the whole is cut into more, smaller pieces.",
        "steps": [
            "Picture the same whole cut into the number of pieces shown by the bottom number.",
            "More pieces means each piece is smaller.",
            "If the top numbers are equal, the fraction with the smaller bottom number is larger.",
            "If the tops differ, rewrite both with a common bottom number, then compare the tops.",
        ],
        "example_lines": [
            f"Example: {a}/{b} or {c}/{d}?",
            f"The same whole is cut into {b} equal parts and into {d} equal parts.",
            f"Pieces get smaller when there are more of them, so {bigger[0]}/{bigger[1]} is larger.",
        ],
        "bars": [_bar(f"{a}/{b}", b, a), _bar(f"{c}/{d}", d, c)],
        "hint": "More pieces in the whole means each piece is smaller.",
    }


def _m04(q: Question) -> dict:
    n, k = next((x for x in NAME_EXAMPLES if x != _key(q)), NAME_EXAMPLES[0])
    return {
        "title": "Reading a fraction: bottom = parts in the whole, top = parts we mean",
        "why": "This may be what happened: the top and bottom numbers were swapped, or the shaded "
               "parts were compared with the unshaded ones. A fraction compares the shaded parts "
               "with ALL the equal parts.",
        "steps": [
            "Count ALL the equal parts in the whole. That is the bottom number.",
            "Count the parts you are talking about (the shaded ones). That is the top number.",
            "Don't leave the shaded parts out of the bottom number.",
        ],
        "example_lines": [
            f"Example: a bar with {n} equal parts, {k} shaded.",
            f"Bottom number = all the equal parts = {n}.",
            f"Top number = shaded parts = {k}.",
            f"The shaded part is {k}/{n}.",
        ],
        "bars": [_bar(f"{k}/{n}", n, k, "shaded parts out of all parts")],
        "hint": "Bottom number: all equal parts. Top number: the shaded parts.",
    }


_BUILDERS = {"M01": _m01, "M02": _m02, "M03": _m03, "M04": _m04, "M05": _m05, "M06": _m06}


def build_intervention(pattern_id: str, q: Question) -> dict:
    out = _BUILDERS[pattern_id](q)
    out.update({"kind": "targeted", "pattern_id": pattern_id,
                "pattern_name": PATTERNS[pattern_id].name})
    return out


def hint_for(pattern_id: str | None, q: Question) -> str:
    if pattern_id in _BUILDERS:
        return _BUILDERS[pattern_id](q)["hint"]
    return general_explanation(q)["hint"]


_GENERAL = {
    "add": ("Adding fractions", [
        "Find a common bottom number that both bottom numbers go into.",
        "Rewrite each fraction with it, multiplying the top and bottom by the same number.",
        "Add the top numbers and keep the bottom number.",
        "Simplify if you can."], "Make the bottom numbers match, then add the tops."),
    "subtract": ("Subtracting fractions", [
        "Find a common bottom number.",
        "Rewrite both fractions with it.",
        "Subtract the top numbers and keep the bottom number.",
        "Simplify if you can."], "Make the bottom numbers match, then subtract the tops."),
    "compare": ("Comparing fractions", [
        "Picture the same whole split into the number of pieces on the bottom.",
        "If the tops are equal, the fraction with fewer, bigger pieces is larger.",
        "Otherwise rewrite both with a common bottom number and compare the tops."],
        "Use a common bottom number, or picture the pieces."),
    "equivalent": ("Equivalent fractions", [
        "Multiply or divide the top and the bottom by the same number.",
        "The amount stays the same; only the number of pieces changes."],
        "Multiply or divide the top and bottom by the same number."),
    "name": ("Naming fractions", [
        "Count all the equal parts: that is the bottom number.",
        "Count the shaded parts: that is the top number."],
        "Bottom: all equal parts. Top: the shaded parts."),
}


def general_explanation(q: Question) -> dict:
    title, steps, hint = _GENERAL[q.skill]
    return {
        "kind": "general", "pattern_id": None, "pattern_name": None,
        "title": f"Let's go over it again: {title.lower()}",
        "why": "We couldn't tell for sure what caused this mix-up, so here is the method "
               "again, followed by a fresh question to try. That is completely normal.",
        "steps": steps, "example_lines": [], "bars": [], "hint": hint,
    }
