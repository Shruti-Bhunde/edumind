"""Train the misconception classifier on simulated learners.

    python -m ml.train --n 20000

Writes models/misconception_clf.joblib and models/metrics.json.
Evaluation uses GroupKFold over *questions*, so the model is always scored on
questions it has never seen. Scores reflect the simulator, NOT real students.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupKFold

from ml.features import FEATURE_NAMES, featurize
from ml.synthetic import simulate

MODEL_VERSION = 1


def build_dataset(n: int, seed: int):
    X, y, groups = [], [], []
    for ex in simulate(n, seed):
        v = featurize(ex.question, ex.submitted)
        if v is None:
            continue
        X.append(v)
        y.append(ex.label)
        groups.append(f"{ex.question.skill}:{sorted(ex.question.payload.items())}")
    return np.array(X), np.array(y), np.array(groups)


def make_model(seed: int = 0) -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=100, min_samples_leaf=4, class_weight="balanced",
                                  random_state=seed, n_jobs=-1)


def train(n: int = 20000, seed: int = 0, out_dir: str = "models", folds: int = 5) -> dict:
    X, y, groups = build_dataset(n, seed)
    pred = np.empty(len(y), dtype=object)
    for tr, te in GroupKFold(n_splits=folds).split(X, y, groups):
        m = make_model(seed).fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    classes = sorted(set(y))
    majority = max(set(y), key=list(y).count)
    metrics = {
        "data": "simulated learners (not real students)",
        "n_examples": int(len(y)),
        "n_unique_questions": int(len(set(groups))),
        "cv": f"GroupKFold({folds}) by question",
        "accuracy": round(float(accuracy_score(y, pred)), 4),
        "macro_f1": round(float(f1_score(y, pred, average="macro")), 4),
        "majority_class_accuracy": round(float(np.mean(y == majority)), 4),
        "per_class": classification_report(y, pred, output_dict=True, zero_division=0),
        "class_counts": {c: int(np.sum(y == c)) for c in classes},
    }
    final = make_model(seed).fit(X, y)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": final, "feature_names": list(FEATURE_NAMES), "classes": list(final.classes_),
                 "version": MODEL_VERSION, "trained_on": "simulated learners", "n": int(len(y)),
                 "seed": seed, "created": datetime.now(timezone.utc).isoformat()},
                out / "misconception_clf.joblib")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--n", type=int, default=20000, help="number of simulated wrong answers")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="models")
    a = ap.parse_args()
    m = train(a.n, a.seed, a.out)
    print(json.dumps({k: v for k, v in m.items() if k != "per_class"}, indent=2))
    print("\nper-class F1:", {k: round(v["f1-score"], 3) for k, v in m["per_class"].items()
                              if k not in ("accuracy", "macro avg", "weighted avg")})
    print("\nNOTE: scores are on SIMULATED learners. Retrain on real data before trusting them.")
