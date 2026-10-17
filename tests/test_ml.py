"""Tests for the ML pipeline: features, simulation, training, the optional hook, and the Eedi baseline."""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.misconceptions import predictions
from app.questions import BANK, BY_ID, by_skill, check_answer
from ml import eedi_baseline
from ml.features import FEATURE_NAMES, IDX, featurize
from ml.synthetic import OTHER, simulate
from ml.train import train


def _as_text(p):
    """Write a predicted wrong answer the way a learner naturally would."""
    from app.fraction_engine import fmt
    if p.typed is not None:
        return p.typed
    return str(p.value) if isinstance(p.value, (int, str)) else fmt(p.value)


def test_features_have_fixed_length_and_reflect_the_mistake():
    q = by_skill("add")[0]
    for p in predictions(q.skill, q.kind, q.payload, q.answer):
        v = featurize(q, _as_text(p))
        assert v.shape == (len(FEATURE_NAMES),)
        if p.pattern_id == "M01":
            assert v[IDX["n_is_tops_combined"]] == 1 and v[IDX["d_is_bottoms_combined"]] == 1
        else:
            assert v[IDX["d_is_lcm"]] == 1
    assert featurize(q, "not a fraction") is None


def test_every_bank_question_featurizes_for_its_wrong_answers():
    for q in BANK:
        for p in predictions(q.skill, q.kind, q.payload, q.answer):
            assert featurize(q, _as_text(p)) is not None, q.id


def test_simulation_only_produces_wrong_answers_with_valid_labels():
    exs = simulate(400, seed=3)
    assert len(exs) == 400
    labels = {e.label for e in exs}
    assert labels <= {OTHER, "M01", "M02", "M03", "M04", "M05", "M06"} and len(labels) >= 5
    for e in exs:
        assert not check_answer(e.question, e.submitted).correct


def test_simulation_is_reproducible():
    a = [(e.submitted, e.label) for e in simulate(100, seed=9)]
    b = [(e.submitted, e.label) for e in simulate(100, seed=9)]
    assert a == b


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    out = tmp_path_factory.mktemp("models")
    metrics = train(n=3000, seed=1, out_dir=str(out), folds=3)
    return out, metrics


def test_training_beats_majority_baseline_on_unseen_questions(trained):
    out, metrics = trained
    assert metrics["accuracy"] > metrics["majority_class_accuracy"] + 0.2
    assert metrics["macro_f1"] > 0.6
    assert (out / "misconception_clf.joblib").exists()
    assert json.loads((out / "metrics.json").read_text())["data"].startswith("simulated")


def test_api_includes_ml_opinion_when_model_present(trained):
    out, _ = trained
    q = by_skill("add")[0]
    wrong = next(p for p in predictions("add", "add", q.payload, q.answer) if p.pattern_id == "M01")
    with TestClient(create_app(":memory:", rate_limit_per_minute=0,
                               model_path=str(out / "misconception_clf.joblib"))) as c:
        lid = c.post("/api/learners").json()["learner_id"]
        r = c.post("/api/attempts", json={"learner_id": lid, "question_id": q.id,
                                          "answer": _as_text(wrong)}).json()
        d = c.post(f"/api/diagnostics/{r['session_id']}/responses",
                   json={"learner_id": lid, "followup_id": "add_bottom", "option_id": "sum"}).json()
    assert d["pattern"]["id"] == "M01"  # the rules decide
    op = d["ml_opinion"]
    assert op["pattern_id"] == "M01" and op["agrees"] is True and 0 < op["probability"] <= 1
    assert "does not change the diagnosis" in op["note"]


def test_corrupt_model_file_is_ignored(tmp_path):
    bad = tmp_path / "bad.joblib"
    bad.write_bytes(b"not a model")
    with TestClient(create_app(":memory:", rate_limit_per_minute=0, model_path=str(bad))) as c:
        assert c.get("/api/health").status_code == 200
        assert c.app.state.service.ml is None


# ----------------------------------------------------------------- Eedi baseline
def _fake_eedi(tmp_path, with_text=True):
    """A TINY FABRICATED fixture with the same columns as the public Kaggle files.
    It exists only to prove the pipeline runs; it is not real student data."""
    mis = {101: "Adds the denominators when adding fractions",
           102: "Believes a larger denominator means a larger fraction",
           103: "Adds the same number to numerator and denominator to make an equivalent fraction"}
    construct = {101: "Add fractions with different denominators",
                 102: "Compare fractions with different denominators",
                 103: "Find equivalent fractions"}
    rows = []
    for i in range(60):
        k = 101 + i % 3
        r = {"QuestionId": i, "ConstructName": construct[k], "SubjectName": "Fractions",
             "QuestionText": f"{construct[k]} (variant {i})", "CorrectAnswer": "A",
             "AnswerAText": "right", "AnswerBText": f"wrong {i}", "AnswerCText": f"other {i}",
             "AnswerDText": f"odd {i}", "MisconceptionAId": np.nan, "MisconceptionBId": k,
             "MisconceptionCId": np.nan, "MisconceptionDId": np.nan}
        rows.append(r)
    pd.DataFrame(rows).to_csv(tmp_path / "train.csv", index=False)
    pd.DataFrame({"MisconceptionId": list(mis), "MisconceptionName": list(mis.values())}) \
        .to_csv(tmp_path / "misconception_mapping.csv", index=False)
    return tmp_path


def test_eedi_baseline_runs_on_fixture(tmp_path):
    res = eedi_baseline.run(str(_fake_eedi(tmp_path)), fractions_only=True)
    assert res["rows_test"] > 0 and res["questions_test"] > 0
    assert res["retrieval_map_at_25"] > 0.3  # construct text overlaps the misconception text
    assert res["classifier_top1"] > 0.9


def test_eedi_baseline_reports_missing_columns(tmp_path):
    d = _fake_eedi(tmp_path)
    df = pd.read_csv(d / "train.csv").drop(columns=["QuestionText"])
    df.to_csv(d / "train.csv", index=False)
    with pytest.raises(eedi_baseline.SchemaError, match="QuestionText"):
        eedi_baseline.run(str(d))


def test_map_at_k():
    assert eedi_baseline.map_at_k([[5, 7, 9]], [7]) == 0.5
    assert eedi_baseline.map_at_k([[5, 7, 9]], [1]) == 0.0
