"""Turn (question, learner answer) into a numeric feature vector.

The features describe *relationships* between the answer and the question's numbers
(e.g. "the answer's top number equals the sum of the two tops"), not the misconception
labels themselves. A model trained on these learns which relationships go with which
mistake, and can be retrained on real data later without touching the rules.
"""
from __future__ import annotations

from fractions import Fraction
from math import gcd

import numpy as np

from app.fraction_engine import AnswerParseError, lcm, parse_fraction, parse_int
from app.questions import SKILLS, Question

SKILL_ORDER = list(SKILLS)

FEATURE_NAMES = (
    [f"skill_{s}" for s in SKILL_ORDER]
    + ["value_ratio", "typed_simplest",
       # add / subtract
       "n_is_tops_combined", "d_is_bottoms_combined", "d_is_lcm", "d_is_product",
       "d_is_first_bottom", "d_is_second_bottom", "n_is_first_converted_only",
       "n_is_second_converted_only", "answer_is_whole", "answer_gt_correct",
       # value-based twins (unaffected by whether the learner simplified)
       "val_tops_bottoms_combined", "val_first_converted_only", "val_second_converted_only",
       "val_is_inverse", "val_is_part_vs_unshaded",
       # compare
       "chose_larger_bottom", "chose_larger_top", "same_tops",
       # equivalent
       "said_no", "ans_is_top", "ans_is_top_plus_diff", "ans_is_new_bottom", "ans_is_old_bottom",
       # name
       "n_is_parts", "d_is_shaded", "n_is_shaded", "d_is_unshaded", "d_is_parts"]
)
IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}


def featurize(q: Question, submitted: str) -> np.ndarray | None:
    """Returns None when the answer can't be read (those never reach diagnosis anyway)."""
    v = np.zeros(len(FEATURE_NAMES), dtype=float)
    v[IDX[f"skill_{q.skill}"]] = 1.0
    p = q.payload
    try:
        if q.skill in ("add", "subtract"):
            f = parse_fraction(submitted)
            n, d, value = f.numerator, f.denominator, f.value
            a, b, c, dd = p["a"], p["b"], p["c"], p["d"]
            L = lcm(b, dd)
            a2, c2 = a * (L // b), c * (L // dd)
            sign = 1 if q.skill == "add" else -1
            v[IDX["n_is_tops_combined"]] = float(n == a + sign * c)
            v[IDX["d_is_bottoms_combined"]] = float(d == (b + dd if sign == 1 else b - dd))
            v[IDX["d_is_lcm"]] = float(d == L)
            v[IDX["d_is_product"]] = float(d == b * dd)
            v[IDX["d_is_first_bottom"]] = float(d == b)
            v[IDX["d_is_second_bottom"]] = float(d == dd)
            v[IDX["n_is_first_converted_only"]] = float(n == a2 + sign * c)
            v[IDX["n_is_second_converted_only"]] = float(n == a + sign * c2)
            v[IDX["answer_is_whole"]] = float(value.denominator == 1)
            v[IDX["answer_gt_correct"]] = float(value > q.answer)
            bd = b + sign * dd
            v[IDX["val_tops_bottoms_combined"]] = float(bd > 0 and value == Fraction(a + sign * c, bd))
            v[IDX["val_first_converted_only"]] = float(value == Fraction(a2 + sign * c, L))
            v[IDX["val_second_converted_only"]] = float(value == Fraction(a + sign * c2, L))
        elif q.skill == "name":
            f = parse_fraction(submitted)
            n, d, value = f.numerator, f.denominator, f.value
            N, k = p["parts"], p["shaded"]
            v[IDX["n_is_parts"]] = float(n == N)
            v[IDX["d_is_shaded"]] = float(d == k)
            v[IDX["n_is_shaded"]] = float(n == k)
            v[IDX["d_is_unshaded"]] = float(d == N - k)
            v[IDX["d_is_parts"]] = float(d == N)
            v[IDX["answer_gt_correct"]] = float(value > q.answer)
            v[IDX["val_is_inverse"]] = float(value == Fraction(N, k))
            v[IDX["val_is_part_vs_unshaded"]] = float(value == Fraction(k, N - k))
        elif q.skill == "compare":
            f = parse_fraction(submitted)
            n, d, value = f.numerator, f.denominator, f.value
            a, b, c, dd = p["a"], p["b"], p["c"], p["d"]
            larger_bottom = (a, b) if b > dd else (c, dd)
            larger_top = (a, b) if a > c else (c, dd)
            v[IDX["chose_larger_bottom"]] = float((n, d) == larger_bottom and b != dd)
            v[IDX["chose_larger_top"]] = float((n, d) == larger_top and a != c)
            v[IDX["same_tops"]] = float(a == c)
        else:  # equivalent
            if q.kind == "yesno":
                v[IDX["said_no"]] = float(submitted.strip().lower() == "no")
                value = None
            else:
                ans = parse_int(submitted)
                a, b, t = p["a"], p["b"], p["t"]
                v[IDX["ans_is_top"]] = float(ans == a)
                v[IDX["ans_is_top_plus_diff"]] = float(ans == a + (t - b))
                v[IDX["ans_is_new_bottom"]] = float(ans == t)
                v[IDX["ans_is_old_bottom"]] = float(ans == b)
                value = Fraction(ans)
        if q.skill != "equivalent" or q.kind == "blank":
            v[IDX["value_ratio"]] = float(min(5.0, max(0.0, float(Fraction(value) / Fraction(q.answer)))))
            if q.skill != "equivalent" and q.skill != "compare":
                v[IDX["typed_simplest"]] = float(gcd(n, d) == 1)
    except (AnswerParseError, ZeroDivisionError):
        return None
    return v
