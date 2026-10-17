"""The TRACE diagnostic engine: Trace reasoning, Rank causes, Apply repair, Check.

Design rules taken from the project plan:
* Diagnosis is a hypothesis, not a label. When evidence is weak or conflicting
  the engine asks another question, and after at most two follow-ups it
  *abstains* and gives a general explanation instead of a targeted one.
* The score is an engineering confidence indicator, not a calibrated probability.
* Everything is rule-based and auditable: each point of evidence is logged.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fraction_engine import fmt
from .misconceptions import PATTERNS, SKILL_PATTERNS, predictions
from .questions import Question

# Tunable thresholds (conservative on purpose).
ANSWER_MATCH_POINTS = 3
CONFIDENT_MIN_SCORE = 3
CONFIDENT_MIN_SHARE = 0.6
CONFIDENT_MIN_MARGIN = 2
MAX_FOLLOWUPS = 2


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    weights: dict  # pattern id -> points; "*" applies to every candidate pattern


@dataclass(frozen=True)
class Followup:
    id: str
    prompt: str
    options: tuple[Option, ...]
    targets: tuple[str, ...]  # patterns this question helps to separate

    def public(self) -> dict:
        return {"id": self.id, "prompt": self.prompt,
                "options": [{"id": o.id, "label": o.label} for o in self.options]}


GUESS = Option("guess", "I'm not sure, or I guessed", {"*": -1})


def followups_for(q: Question) -> list[Followup]:
    """Neutral, multiple-choice questions about the learner's thinking."""
    p = q.payload
    if q.skill == "add":
        return [
            Followup("add_bottom", "How did you work out the bottom number in your answer?", (
                Option("sum", "I added the two bottom numbers together", {"M01": 2, "M05": -1}),
                Option("common", "I found a number that both bottom numbers go into",
                       {"M05": 1, "M01": -2}),
                Option("kept", "I kept one of the bottom numbers", {"M01": 1}),
                GUESS), ("M01", "M05")),
            Followup("add_top", "When you rewrite a fraction with a new bottom number, "
                                "what should happen to its top number?", (
                Option("same", "It stays the same", {"M05": 2}),
                Option("mult", "It is multiplied by the same number as the bottom", {"M05": -2}),
                Option("plus", "The same number is added to it", {"M05": 1}),
                GUESS), ("M05",)),
        ]
    if q.skill == "subtract":
        return [
            Followup("sub_bottom", "What did you do with the two bottom numbers?", (
                Option("diff", "I subtracted them", {"M06": 2, "M05": -1}),
                Option("common", "I found a number that both of them go into",
                       {"M05": 1, "M06": -2}),
                Option("kept", "I kept one of them", {"M06": 1}),
                GUESS), ("M06", "M05")),
            Followup("sub_size", "Before subtracting, are the pieces in the two fractions "
                                 "the same size?", (
                Option("already", "Yes, they were already the same size", {"M06": 2}),
                Option("changed", "No, so I changed them to be the same size",
                       {"M05": 1, "M06": -1}),
                Option("nothought", "I didn't think about it", {"M06": 1}),
                GUESS), ("M06",)),
            Followup("sub_top", "When you rewrite a fraction with a new bottom number, "
                                "what should happen to its top number?", (
                Option("same", "It stays the same", {"M05": 2}),
                Option("mult", "It is multiplied by the same number as the bottom", {"M05": -2}),
                Option("plus", "The same number is added to it", {"M05": 1}),
                GUESS), ("M05",)),
        ]
    if q.skill == "compare":
        big = max(p["b"], p["d"])
        small = min(p["b"], p["d"])
        return [
            Followup("cmp_how", "How did you decide which fraction is larger?", (
                Option("bottom", "The bigger bottom number means the bigger fraction", {"M03": 2}),
                Option("picture", "I pictured or drew the pieces", {"M03": -2}),
                Option("top", "I compared the top numbers", {}),
                GUESS), ("M03",)),
            Followup("cmp_pizza", f"Two identical pizzas: one is cut into {big} equal slices and the "
                                  f"other into {small} equal slices. Which slices are bigger?", (
                Option("many", f"Slices from the pizza cut into {big}", {"M03": 2}),
                Option("few", f"Slices from the pizza cut into {small}", {"M03": -2}),
                Option("same", "They are the same size", {}),
                GUESS), ("M03",)),
        ]
    if q.skill == "equivalent":
        return [
            Followup("eq_how", "What was your thinking?", (
                Option("diff", "Different-looking numbers must mean different amounts", {"M02": 2}),
                Option("add", "I added the same number to the top and the bottom", {"M02": 2}),
                Option("mult", "I multiplied or divided the top and bottom by the same number",
                       {"M02": -2}),
                GUESS), ("M02",)),
            Followup("eq_pizza", "A pizza is cut into twice as many equal slices and you take twice "
                                 "as many slices. Do you eat the same amount of pizza?", (
                Option("yes", "Yes, the same amount", {"M02": -2}),
                Option("no", "No, a different amount", {"M02": 2}),
                GUESS), ("M02",)),
        ]
    # name
    return [
        Followup("nm_bottom", "What does the bottom number of a fraction tell us?", (
            Option("whole", "How many equal parts the whole is split into", {"M04": -2}),
            Option("shaded", "How many parts are shaded or chosen", {"M04": 2}),
            Option("unshaded", "How many parts are not shaded", {"M04": 2}),
            GUESS), ("M04",)),
        Followup("nm_top", "What does the top number of a fraction tell us?", (
            Option("chosen", "How many of the equal parts we are talking about, e.g. the shaded ones",
                   {"M04": -2}),
            Option("whole", "How many parts the whole is split into", {"M04": 2}),
            Option("unshaded", "How many parts are not shaded", {"M04": 2}),
            GUESS), ("M04",)),
    ]


def _display(value: object) -> str:
    try:
        return fmt(value)  # type: ignore[arg-type]
    except Exception:
        return str(value)


def initial_scores(q: Question, normalized: object) -> tuple[dict[str, int], list[str]]:
    """Score the answer pattern: +3 for each misconception whose predicted wrong answer matches."""
    scores = {pid: 0 for pid in SKILL_PATTERNS[q.skill]}
    evidence: list[str] = []
    for pred in predictions(q.skill, q.kind, q.payload, q.answer):
        if pred.value == normalized:
            scores[pred.pattern_id] += ANSWER_MATCH_POINTS
            evidence.append(
                f"Your answer ({_display(normalized)}) is what you get if you {pred.reason} "
                f"(+{ANSWER_MATCH_POINTS} for “{PATTERNS[pred.pattern_id].name}”)."
            )
    if not evidence:
        evidence.append("Your answer didn't match any of the specific patterns we look for, "
                        "so your explanation matters more.")
    return scores, evidence


def apply_followup(scores: dict[str, int], evidence: list[str],
                   followup: Followup, option: Option) -> None:
    changes = []
    for pid, w in option.weights.items():
        targets = list(scores) if pid == "*" else ([pid] if pid in scores else [])
        for t in targets:
            scores[t] += w
            changes.append(f"{PATTERNS[t].name} {w:+d}")
    detail = ", ".join(changes) if changes else "no change"
    evidence.append(f"You said “{option.label}” ({detail}).")


@dataclass(frozen=True)
class Decision:
    status: str  # ask_more | targeted | general
    pattern_id: str | None
    top_score: int
    share: float
    margin: int


def decide(scores: dict[str, int], n_asked: int) -> Decision:
    pos = {k: max(0, v) for k, v in scores.items()}
    total = sum(pos.values())
    ranked = sorted(pos.items(), key=lambda kv: (-kv[1], kv[0]))
    top_id, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    share = (top / total) if total else 0.0
    margin = top - second
    if (top >= CONFIDENT_MIN_SCORE and share >= CONFIDENT_MIN_SHARE
            and margin >= CONFIDENT_MIN_MARGIN):
        return Decision("targeted", top_id, top, round(share, 2), margin)
    if n_asked < MAX_FOLLOWUPS:
        return Decision("ask_more", None, top, round(share, 2), margin)
    return Decision("general", None, top, round(share, 2), margin)


def choose_followup(q: Question, scores: dict[str, int], asked_ids: list[str]) -> Followup | None:
    """Next question: prefer the one that helps separate the current leading candidate."""
    remaining = [f for f in followups_for(q) if f.id not in asked_ids]
    if not remaining:
        return None
    pos = {k: max(0, v) for k, v in scores.items()}
    leader = max(pos.items(), key=lambda kv: (kv[1], kv[0]))
    if leader[1] > 0:
        for f in remaining:
            if leader[0] in f.targets:
                return f
    return remaining[0]
