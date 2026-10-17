"""Optional second opinion from the trained classifier.

The rule-based engine always makes the decision. If a trained model file exists, its
view is shown (clearly labelled experimental) so you can compare the two. Delete the
file, or point EDUMIND_MODEL elsewhere, to switch it off.

Security note: model files are pickles. Only load files you trained yourself.
"""
from __future__ import annotations

from pathlib import Path

from .misconceptions import PATTERNS
from .questions import Question

NOTE = ("Experimental model trained on simulated learners. Shown for comparison only; "
        "it does not change the diagnosis.")


class MLOpinion:
    def __init__(self, path: str):
        import joblib
        from ml.features import FEATURE_NAMES

        bundle = joblib.load(path)
        if bundle.get("feature_names") != list(FEATURE_NAMES):
            raise ValueError("Model was trained with different features; retrain with python -m ml.train")
        self.model = bundle["model"]
        self.classes = list(bundle["classes"])
        self.trained_on = bundle.get("trained_on", "unknown")

    @classmethod
    def load_if_present(cls, path: str | None) -> "MLOpinion | None":
        if path and Path(path).exists():
            try:
                return cls(path)
            except Exception as exc:  # a broken model file must never break the app
                print(f"[edumind] ML model ignored: {exc}")
        return None

    def opinion(self, q: Question, submitted: str) -> dict | None:
        from ml.features import featurize

        v = featurize(q, submitted)
        if v is None:
            return None
        proba = self.model.predict_proba([v])[0]
        i = int(proba.argmax())
        label = self.classes[i]
        return {
            "pattern_id": label if label in PATTERNS else None,
            "name": PATTERNS[label].name if label in PATTERNS else "no specific pattern",
            "probability": round(float(proba[i]), 2),
            "note": NOTE,
        }
