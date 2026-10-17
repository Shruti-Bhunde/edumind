"""End-to-end tests of the HTTP API: the full TRACE loop, validation and privacy."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.misconceptions import predictions
from app.questions import BANK, BY_ID, by_skill


@pytest.fixture()
def client():
    with TestClient(create_app(":memory:", rate_limit_per_minute=0, model_path="")) as c:
        yield c


@pytest.fixture()
def learner(client):
    return client.post("/api/learners").json()["learner_id"]


def wrong_answer(q, pattern_id=None):
    preds = predictions(q.skill, q.kind, q.payload, q.answer)
    p = next(p for p in preds if pattern_id is None or p.pattern_id == pattern_id)
    from app.fraction_engine import fmt
    if p.typed is not None:
        return p.typed
    v = p.value
    return fmt(v) if not isinstance(v, (int, str)) else str(v)


def attempt(client, learner, qid, answer):
    return client.post("/api/attempts", json={"learner_id": learner, "question_id": qid, "answer": answer})


def respond(client, learner, sid, followup_id, option_id):
    return client.post(f"/api/diagnostics/{sid}/responses",
                       json={"learner_id": learner, "followup_id": followup_id, "option_id": option_id})


def test_health_and_frontend(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "EduMind" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_correct_answer_flow(client, learner):
    q = by_skill("name")[0]
    r = attempt(client, learner, q.id, q.answer_text)
    assert r.status_code == 200 and r.json()["correct"] is True


def test_full_trace_loop_targeted(client, learner):
    q = by_skill("add")[0]
    r = attempt(client, learner, q.id, wrong_answer(q, "M01")).json()
    assert r["correct"] is False and "answer" not in r
    sid, f1 = r["session_id"], r["followup"]
    assert f1["id"] == "add_bottom"

    d = respond(client, learner, sid, f1["id"], "sum").json()
    assert d["status"] == "resolved" and d["outcome"] == "targeted"
    assert d["pattern"]["id"] == "M01"
    assert d["confidence_indicator"]["label"] == "Likely"
    assert d["intervention"]["steps"] and d["intervention"]["bars"]
    assert any("bottoms separately" in e for e in d["evidence"])
    # the worked example must not be the learner's own question
    assert f"{q.payload['a']}/{q.payload['b']} + {q.payload['c']}/{q.payload['d']}" \
        not in d["intervention"]["example_lines"][0]

    mq = d["mastery_question"]
    assert mq["id"] != q.id and mq["skill"] == "add" and "answer" not in mq
    m = client.post("/api/mastery-checks", json={
        "learner_id": learner, "session_id": sid, "question_id": mq["id"],
        "answer": BY_ID[mq["id"]].answer_text}).json()
    assert m["correct"] and m["outcome"] == "mastered"

    prog = client.get("/api/progress", params={"learner_id": learner}).json()
    mis = prog["misconceptions"][0]
    assert (mis["pattern_id"], mis["times_seen"], mis["resolved"]) == ("M01", 1, 1)
    add = next(s for s in prog["skills"] if s["skill"] == "add")
    assert add["attempts"] == 2 and add["correct"] == 1


def test_inconsistent_evidence_gives_general_explanation(client, learner):
    q = by_skill("add")[0]
    r = attempt(client, learner, q.id, wrong_answer(q, "M01")).json()
    sid = r["session_id"]
    r2 = respond(client, learner, sid, "add_bottom", "common").json()
    assert r2["status"] == "needs_followup"
    r3 = respond(client, learner, sid, r2["followup"]["id"], "mult").json()
    assert r3["status"] == "resolved" and r3["outcome"] == "general"
    assert r3["pattern"] is None and r3["confidence_indicator"]["label"] == "Not sure"
    assert r3["intervention"]["kind"] == "general"


def test_failed_mastery_checks_schedule_review(client, learner):
    q = by_skill("compare")[0]
    r = attempt(client, learner, q.id, wrong_answer(q)).json()
    sid = r["session_id"]
    d = respond(client, learner, sid, r["followup"]["id"], "bottom").json()
    assert d["pattern"]["id"] == "M03"
    mq = d["mastery_question"]

    def wrong_mastery(mq):
        mqq = BY_ID[mq["id"]]
        return client.post("/api/mastery-checks", json={
            "learner_id": learner, "session_id": sid, "question_id": mq["id"],
            "answer": wrong_answer(mqq)}).json()

    first = wrong_mastery(mq)
    assert first["outcome"] == "retry" and first["hint"]
    second = wrong_mastery(first["mastery_question"])
    assert second["outcome"] == "review_scheduled" and second["answer"]
    prog = client.get("/api/progress", params={"learner_id": learner}).json()
    assert len(prog["review"]) == 1 and prog["review"][0]["skill"] == "compare"
    assert prog["review"][0]["due"] is False
    # finished sessions can't be reused
    assert client.post("/api/mastery-checks", json={
        "learner_id": learner, "session_id": sid, "question_id": first["mastery_question"]["id"],
        "answer": "1/2"}).status_code == 409


def test_unreadable_answers_are_rejected_and_not_recorded(client, learner):
    q = by_skill("add")[0]
    for bad in ["abc", "", "1/0", "1.5"]:
        assert attempt(client, learner, q.id, bad).status_code == 422
    assert client.get("/api/progress", params={"learner_id": learner}).json()["totals"]["attempts"] == 0


def test_validation_and_errors(client, learner):
    q = by_skill("add")[0]
    assert attempt(client, "00000000-0000-0000-0000-000000000000", q.id, "1/2").status_code == 404
    assert attempt(client, learner, "nope", "1/2").status_code == 404
    assert attempt(client, "not-a-uuid", q.id, "1/2").status_code == 422
    assert attempt(client, learner, q.id, "1" * 40).status_code == 422
    assert client.get("/api/next-question", params={"learner_id": learner, "skill": "zzz"}).status_code == 400


def test_followup_must_be_the_one_waiting(client, learner):
    q = by_skill("add")[0]
    sid = attempt(client, learner, q.id, wrong_answer(q, "M01")).json()["session_id"]
    assert respond(client, learner, sid, "add_top", "same").status_code == 409  # not the waiting one
    assert respond(client, learner, sid, "add_bottom", "bogus").status_code == 400
    assert respond(client, learner, sid, "add_bottom", "sum").status_code == 200
    assert respond(client, learner, sid, "add_bottom", "sum").status_code == 409  # already finished


def test_sessions_are_private_to_their_learner(client, learner):
    other = client.post("/api/learners").json()["learner_id"]
    q = by_skill("add")[0]
    sid = attempt(client, learner, q.id, wrong_answer(q, "M01")).json()["session_id"]
    assert respond(client, other, sid, "add_bottom", "sum").status_code == 404


def test_next_question_covers_bank_then_repeats_without_immediate_repeat(client, learner):
    seen = []
    for _ in range(len(by_skill("name")) + 3):
        q = client.get("/api/next-question", params={"learner_id": learner, "skill": "name"}).json()
        assert "answer" not in q and "payload" not in q
        if seen:
            assert q["id"] != seen[-1]
        seen.append(q["id"])
        attempt(client, learner, q["id"], BY_ID[q["id"]].answer_text)
    assert set(seen[: len(by_skill("name"))]) == {x.id for x in by_skill("name")}


def test_auto_skill_selection_prefers_unstarted_skills(client, learner):
    q = client.get("/api/next-question", params={"learner_id": learner}).json()
    attempt(client, learner, q["id"], BY_ID[q["id"]].answer_text)
    q2 = client.get("/api/next-question", params={"learner_id": learner}).json()
    assert q2["skill"] != q["skill"]


def test_delete_learner_erases_everything(client, learner):
    q = by_skill("add")[0]
    attempt(client, learner, q.id, wrong_answer(q, "M01"))
    assert client.delete(f"/api/learners/{learner}").status_code == 200
    assert client.get("/api/progress", params={"learner_id": learner}).status_code == 404
    db = client.app.state.service.db
    for table in ("attempts", "diagnostic_sessions", "mastery_checks", "review_queue", "learners"):
        assert db.one(f"SELECT COUNT(*) n FROM {table}")["n"] == 0


def test_quiz_generation_and_grading(client):
    qs = client.get("/api/quiz", params={"skill": "compare", "n": 4, "seed": 1}).json()["questions"]
    assert len(qs) == 4 and all(q["skill"] == "compare" for q in qs)
    body = {"answers": [{"question_id": qs[0]["id"], "answer": BY_ID[qs[0]["id"]].answer_text},
                        {"question_id": qs[1]["id"], "answer": "junk"}]}
    g = client.post("/api/quiz/grade", json=body).json()
    assert g["score"] == 1 and g["total"] == 2 and g["results"][1]["correct"] is False
    assert client.get("/api/quiz", params={"n": 99}).status_code == 422


def test_intervention_endpoint(client):
    q = by_skill("add")[0]
    r = client.get("/api/patterns/M01/intervention", params={"question_id": q.id})
    assert r.status_code == 200 and r.json()["pattern_id"] == "M01"
    assert client.get("/api/patterns/M03/intervention", params={"question_id": q.id}).status_code == 400
    assert client.get("/api/patterns/M99/intervention", params={"question_id": q.id}).status_code == 400


def test_rate_limit():
    with TestClient(create_app(":memory:", rate_limit_per_minute=3)) as c:
        codes = [c.get("/api/health").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_wrong_yes_on_unequal_pair_skips_diagnosis(client, learner):
    """No misconception predicts this wrong answer, so the engine must not guess."""
    q = next(x for x in by_skill("equivalent") if x.kind == "yesno" and x.answer == "no")
    r = attempt(client, learner, q.id, "yes").json()
    assert r["correct"] is False and "followup" not in r
    res = r["resolved"]
    assert res["outcome"] == "general" and res["pattern"] is None
    assert res["intervention"]["kind"] == "general"
    mq = res["mastery_question"]
    ok = client.post("/api/mastery-checks", json={
        "learner_id": learner, "session_id": r["session_id"], "question_id": mq["id"],
        "answer": BY_ID[mq["id"]].answer_text.lower() if mq["kind"] == "yesno" else BY_ID[mq["id"]].answer_text})
    assert ok.status_code == 200 and ok.json()["correct"] is True
