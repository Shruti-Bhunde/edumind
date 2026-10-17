"""Baselines for the Eedi "Mining Misconceptions in Mathematics" data (Kaggle).

    python -m ml.eedi_baseline --data-dir path/to/eedi [--fractions-only]

Download the files from Kaggle first (a free account and accepting the competition
rules are required):
    https://www.kaggle.com/competitions/eedi-mining-misconceptions-in-mathematics/data

Expected files in --data-dir:
    train.csv                  one row per question; columns include QuestionId, ConstructName,
                               SubjectName, QuestionText, CorrectAnswer (A-D), AnswerAText..AnswerDText,
                               MisconceptionAId..MisconceptionDId
    misconception_mapping.csv  MisconceptionId, MisconceptionName

The script checks the columns and tells you exactly what is missing if Kaggle changed
the format. Two baselines are scored on held-out questions:
  * retrieval  - TF-IDF similarity between (question + wrong answer) and misconception text
                 (the competition task; metric MAP@25)
  * classifier - logistic regression over the misconceptions that appear in training
                 (top-1 / top-5 accuracy on held-out rows whose label was seen in training)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit

LETTERS = "ABCD"
REQUIRED_TRAIN = (["QuestionId", "ConstructName", "SubjectName", "QuestionText", "CorrectAnswer"]
                  + [f"Answer{x}Text" for x in LETTERS] + [f"Misconception{x}Id" for x in LETTERS])
REQUIRED_MAP = ["MisconceptionId", "MisconceptionName"]


class SchemaError(ValueError):
    pass


def _check(df: pd.DataFrame, required: list[str], name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SchemaError(f"{name} is missing columns {missing}. Found: {list(df.columns)}")


def load_rows(data_dir: str, fractions_only: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = Path(data_dir)
    train = pd.read_csv(d / "train.csv")
    mapping = pd.read_csv(d / "misconception_mapping.csv")
    _check(train, REQUIRED_TRAIN, "train.csv")
    _check(mapping, REQUIRED_MAP, "misconception_mapping.csv")
    if fractions_only:
        text = (train["ConstructName"].fillna("") + " " + train["SubjectName"].fillna("")).str.lower()
        train = train[text.str.contains("fraction")]
    rows = []
    for _, r in train.iterrows():
        correct = str(r["CorrectAnswer"]).strip().upper()
        if correct not in LETTERS:
            continue
        for x in LETTERS:
            mid = r[f"Misconception{x}Id"]
            if x == correct or pd.isna(mid):
                continue
            rows.append({
                "QuestionId": r["QuestionId"], "MisconceptionId": int(mid),
                "text": f"{r['ConstructName']} | {r['SubjectName']} | {r['QuestionText']} | "
                        f"correct: {r[f'Answer{correct}Text']} | wrong: {r[f'Answer{x}Text']}"})
    return pd.DataFrame(rows), mapping


def map_at_k(ranked_ids: list[list[int]], truth: list[int], k: int = 25) -> float:
    total = 0.0
    for ranks, t in zip(ranked_ids, truth):
        top = ranks[:k]
        if t in top:
            total += 1.0 / (top.index(t) + 1)
    return total / max(1, len(truth))


def run(data_dir: str, fractions_only: bool = False, seed: int = 0, test_size: float = 0.2) -> dict:
    rows, mapping = load_rows(data_dir, fractions_only)
    if len(rows) < 10:
        raise SchemaError(f"Only {len(rows)} usable rows found; check --data-dir / --fractions-only.")
    split = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    tr_idx, te_idx = next(split.split(rows, groups=rows["QuestionId"]))
    tr, te = rows.iloc[tr_idx], rows.iloc[te_idx]

    vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", min_df=1)
    vec.fit(list(rows["text"]) + list(mapping["MisconceptionName"]))
    M = vec.transform(mapping["MisconceptionName"])
    ids = mapping["MisconceptionId"].to_numpy()

    # 1) retrieval: rank every misconception for each held-out (question, wrong answer)
    sims = (vec.transform(te["text"]) @ M.T).toarray()
    ranked = [list(ids[np.argsort(-row)[:25]]) for row in sims]
    result = {
        "rows_train": int(len(tr)), "rows_test": int(len(te)),
        "questions_test": int(te["QuestionId"].nunique()),
        "retrieval_map_at_25": round(map_at_k(ranked, list(te["MisconceptionId"])), 4),
        "retrieval_recall_at_25": round(float(np.mean([t in r for r, t in zip(ranked, te["MisconceptionId"])])), 4),
    }

    # 2) classifier over misconceptions seen at least twice in training
    counts = tr["MisconceptionId"].value_counts()
    keep = counts[counts >= 2].index
    sub = tr[tr["MisconceptionId"].isin(keep)]
    seen_te = te[te["MisconceptionId"].isin(keep)]
    result["classifier_labels"] = int(len(keep))
    result["classifier_test_coverage"] = round(len(seen_te) / max(1, len(te)), 4)
    if len(keep) >= 2 and len(seen_te):
        clf = LogisticRegression(max_iter=1000, C=5.0).fit(vec.transform(sub["text"]), sub["MisconceptionId"])
        proba = clf.predict_proba(vec.transform(seen_te["text"]))
        order = np.argsort(-proba, axis=1)
        top = clf.classes_[order]
        truth = seen_te["MisconceptionId"].to_numpy()
        result["classifier_top1"] = round(float(np.mean(top[:, 0] == truth)), 4)
        result["classifier_top5"] = round(float(np.mean([t in row[:5] for row, t in zip(top, truth)])), 4)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--fractions-only", action="store_true",
                    help="keep only constructs/subjects whose name mentions 'fraction'")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    print(json.dumps(run(a.data_dir, a.fractions_only, a.seed), indent=2))
