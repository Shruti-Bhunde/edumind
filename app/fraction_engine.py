"""Deterministic fraction maths.

Every answer the learner gives is parsed and checked here, in plain Python.
No language model is involved in deciding whether an answer is correct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from math import gcd


class AnswerParseError(ValueError):
    """Raised when a learner's text cannot be read as an answer we accept."""


_INT = re.compile(r"^\d{1,4}$")
_FRAC = re.compile(r"^(\d{1,4})\s*/\s*(\d{1,4})$")
_MIXED = re.compile(r"^(\d{1,4})\s+(\d{1,4})\s*/\s*(\d{1,4})$")

_HELP = "Please type a fraction like 3/4, a whole number like 2, or a mixed number like 1 1/2."


@dataclass(frozen=True)
class ParsedFraction:
    """A fraction as the learner typed it (kept unsimplified) plus its exact value."""

    value: Fraction
    numerator: int
    denominator: int

    @property
    def in_simplest_form(self) -> bool:
        return gcd(self.numerator, self.denominator) == 1


def parse_fraction(text: str) -> ParsedFraction:
    """Parse '3/4', '2', '1 1/2' (also '3 / 4' and the Unicode fraction slash)."""
    if not isinstance(text, str):
        raise AnswerParseError(_HELP)
    s = text.strip().replace("⁄", "/").replace("∕", "/")
    if not s:
        raise AnswerParseError("Please type an answer first.")

    if _INT.match(s):
        n = int(s)
        return ParsedFraction(Fraction(n), n, 1)

    m = _FRAC.match(s)
    if m:
        n, d = int(m.group(1)), int(m.group(2))
        if d == 0:
            raise AnswerParseError("The bottom number of a fraction can't be 0.")
        return ParsedFraction(Fraction(n, d), n, d)

    m = _MIXED.match(s)
    if m:
        w, n, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if d == 0:
            raise AnswerParseError("The bottom number of a fraction can't be 0.")
        top = w * d + n
        return ParsedFraction(Fraction(top, d), top, d)

    raise AnswerParseError(_HELP)


def parse_int(text: str) -> int:
    if not isinstance(text, str) or not _INT.match(text.strip()):
        raise AnswerParseError("Please type a whole number, like 3.")
    return int(text.strip())


def fmt(value: Fraction) -> str:
    """'5/6', or just '2' for whole numbers."""
    value = Fraction(value)
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def lcm(a: int, b: int) -> int:
    return a * b // gcd(a, b)
