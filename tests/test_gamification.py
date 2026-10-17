"""Comprehensive tests for EduMind AI Gamification features:
- Badges and unlock criteria
- Daily learning streaks and idempotency
- Daily quests, progression, and rewards
- Privacy and learner deletion
"""
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.db import Database
from app.gamification import GamificationEngine, BADGE_CATALOG
from app.main import create_app
from app.service import EduMindService


def make_service(clock=None):
    db = Database(":memory:")
    return EduMindService(db, clock=clock)


def test_badge_catalog_integrity():
    assert "first_steps" in BADGE_CATALOG
    assert "perfect_score" in BADGE_CATALOG
    assert "quiz_champion" in BADGE_CATALOG
    assert "consistent_learner" in BADGE_CATALOG
    assert "week_warrior" in BADGE_CATALOG
    assert "daily_quest_hero" in BADGE_CATALOG
    assert "mastery_achieved" in BADGE_CATALOG
    assert "fraction_explorer" in BADGE_CATALOG
    assert "accuracy_star" in BADGE_CATALOG
    assert "daily_practice" in BADGE_CATALOG

    for bid, bdef in BADGE_CATALOG.items():
        assert bdef.id == bid
        assert len(bdef.name) > 0
        assert len(bdef.description) > 0
        assert len(bdef.icon) > 0
        assert len(bdef.unlock_condition) > 0


def test_streak_first_day_and_same_day():
    day1 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    current_time = day1

    def clock():
        return current_time

    service = make_service(clock=clock)
    lid = service.create_learner()

    # Day 1: complete a quiz
    q = service.quiz(n=3, skill="add", difficulty=1, seed=42)
    answers = [{"question_id": x["id"], "answer": x["prompt"]} for x in q]  # might not all be right
    # Let's provide correct answers by checking
    from app.questions import get_question
    answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in q]

    res1 = service.grade_quiz(answers, learner_id=lid)
    assert res1["score"] == 3
    summary1 = service.get_gamification_summary(lid)
    assert summary1["streak"]["current"] == 1
    assert summary1["streak"]["longest"] == 1
    assert summary1["streak"]["practiced_today"] is True

    # Same day another quiz: streak must NOT increase
    current_time = day1 + timedelta(hours=3)
    res2 = service.grade_quiz(answers, learner_id=lid)
    summary2 = service.get_gamification_summary(lid)
    assert summary2["streak"]["current"] == 1
    assert summary2["streak"]["longest"] == 1


def test_streak_consecutive_days_and_milestones():
    start_dt = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
    current_time = start_dt

    def clock():
        return current_time

    service = make_service(clock=clock)
    lid = service.create_learner()

    from app.questions import get_question

    # Days 1 to 7 consecutive practice
    for day in range(7):
        current_time = start_dt + timedelta(days=day)
        q = service.quiz(n=3, skill="add", difficulty=1, seed=day)
        answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in q]
        service.grade_quiz(answers, learner_id=lid)
        summary = service.get_gamification_summary(lid)
        assert summary["streak"]["current"] == day + 1
        assert summary["streak"]["longest"] == day + 1

    summary = service.get_gamification_summary(lid)
    assert summary["streak"]["current"] == 7
    assert summary["streak"]["longest"] == 7

    earned = {b["id"] for b in summary["badges"] if b["earned"]}
    assert "consistent_learner" in earned  # 3-day streak
    assert "week_warrior" in earned  # 7-day streak
    assert "first_steps" in earned
    assert "quiz_champion" in earned  # 5+ quizzes


def test_streak_reset_after_missed_day_preserves_longest():
    start_dt = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
    current_time = start_dt

    def clock():
        return current_time

    service = make_service(clock=clock)
    lid = service.create_learner()
    from app.questions import get_question

    # Day 1, 2, 3: consecutive practice -> streak = 3
    for day in range(3):
        current_time = start_dt + timedelta(days=day)
        q = service.quiz(n=3, skill="add", difficulty=1, seed=day)
        answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in q]
        service.grade_quiz(answers, learner_id=lid)

    summary = service.get_gamification_summary(lid)
    assert summary["streak"]["current"] == 3
    assert summary["streak"]["longest"] == 3

    # Skip 2 full days: from Day 3 (Oct 3) to Day 6 (Oct 6)
    current_time = start_dt + timedelta(days=5)  # Oct 6
    summary_before = service.get_gamification_summary(lid)
    assert summary_before["streak"]["current"] == 0  # dropped to 0 because inactive for > 1 day

    # Next activity on Day 6: streak starts over at 1, but longest streak remains 3!
    q = service.quiz(n=3, skill="add", difficulty=1, seed=99)
    answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in q]
    service.grade_quiz(answers, learner_id=lid)

    summary_after = service.get_gamification_summary(lid)
    assert summary_after["streak"]["current"] == 1
    assert summary_after["streak"]["longest"] == 3  # Longest streak preserved!


def test_quiz_completion_idempotency():
    day = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    service = make_service(clock=lambda: day)
    lid = service.create_learner()
    from app.questions import get_question

    q = service.quiz(n=3, skill="add", difficulty=1, seed=12)
    answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in q]

    idemp_key = "unique-test-sub-12345"
    res1 = service.grade_quiz(answers, learner_id=lid, idempotency_key=idemp_key)
    assert res1["gamification"]["already_processed"] is False
    assert len(res1["gamification"]["newly_unlocked_badges"]) > 0

    # Duplicate request with same key
    res2 = service.grade_quiz(answers, learner_id=lid, idempotency_key=idemp_key)
    assert res2["gamification"]["already_processed"] is True
    assert len(res2["gamification"]["newly_unlocked_badges"]) == 0

    # Stats have not double counted
    summary = service.get_gamification_summary(lid)
    assert summary["stats"]["quizzes_completed"] == 1


def test_daily_quests_stability_and_completion():
    day = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    service = make_service(clock=lambda: day)
    lid = service.create_learner()
    from app.questions import get_question

    # Quests exist and are stable
    q1 = service.get_daily_quests(lid)
    q2 = service.get_daily_quests(lid)
    assert len(q1) == 3
    assert [x["quest_id"] for x in q1] == [x["quest_id"] for x in q2]
    assert all(x["status"] == "Not Started" for x in q1)

    # Complete 1 quiz with 100%
    quiz_items = service.quiz(n=3, skill="add", difficulty=1, seed=1)
    answers = [{"question_id": x["id"], "answer": get_question(x["id"]).answer_text} for x in quiz_items]
    res = service.grade_quiz(answers, learner_id=lid)

    unlocked_ids = {b["id"] for b in res["gamification"]["newly_unlocked_badges"]}
    assert "fraction_explorer" in unlocked_ids
    assert "accuracy_star" in unlocked_ids
    assert "daily_quest_hero" in unlocked_ids

    # Check updated quests
    updated_quests = service.get_daily_quests(lid)
    quest_map = {q["quest_id"]: q for q in updated_quests}
    assert quest_map["quest_fraction_explorer"]["status"] == "Completed"
    assert quest_map["quest_accuracy"]["status"] == "Completed"
    assert quest_map["quest_daily_practice"]["status"] == "In Progress"
    assert quest_map["quest_daily_practice"]["progress"] == 1

    # Complete 2nd quiz
    res2 = service.grade_quiz(answers, learner_id=lid)
    unlocked_ids2 = {b["id"] for b in res2["gamification"]["newly_unlocked_badges"]}
    assert "daily_practice" in unlocked_ids2

    quests_final = service.get_daily_quests(lid)
    assert all(q["status"] == "Completed" for q in quests_final)


def test_mastery_achievement_badge():
    service = make_service()
    lid = service.create_learner()
    from app.questions import by_skill, get_question
    from app.misconceptions import predictions
    from app.fraction_engine import fmt

    q = by_skill("add")[0]
    p = next(p for p in predictions(q.skill, q.kind, q.payload, q.answer) if p.pattern_id == "M01")
    ans = p.typed if p.typed is not None else (fmt(p.value) if not isinstance(p.value, (int, str)) else str(p.value))

    att = service.submit_attempt(lid, q.id, ans)
    sid = att["session_id"]
    fu = att["followup"]

    resp = service.respond_followup(lid, sid, fu["id"], "sum")
    assert resp["status"] == "resolved"
    mq = resp["mastery_question"]
    mq_obj = get_question(mq["id"])

    # Solve fresh mastery check correctly
    m_res = service.submit_mastery(lid, sid, mq["id"], mq_obj.answer_text)
    assert m_res["correct"] is True
    assert m_res["outcome"] == "mastered"
    unlocked = {b["id"] for b in m_res.get("newly_unlocked_badges", [])}
    assert "mastery_achieved" in unlocked


def test_gamification_api_endpoints():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="")
    with TestClient(app) as client:
        lid = client.post("/api/learners").json()["learner_id"]

        # Summary
        r = client.get(f"/api/gamification/summary?learner_id={lid}")
        assert r.status_code == 200
        data = r.json()
        assert "streak" in data
        assert "badges" in data
        assert "quests" in data
        assert data["streak"]["current"] == 0
        assert data["earned_count"] == 0

        # Quests
        rq = client.get(f"/api/gamification/quests?learner_id={lid}")
        assert rq.status_code == 200
        assert len(rq.json()["quests"]) == 3

        # Grade quiz with learner_id
        q_data = client.get("/api/quiz?n=3").json()["questions"]
        answers = [{"question_id": x["id"], "answer": "1"} for x in q_data]
        rg = client.post("/api/quiz/grade", json={
            "learner_id": lid,
            "answers": answers,
        })
        assert rg.status_code == 200
        grade_data = rg.json()
        assert "gamification" in grade_data
        assert "summary" in grade_data["gamification"]

        # Delete learner cleans up gamification tables
        rdel = client.delete(f"/api/learners/{lid}")
        assert rdel.status_code == 200

        # Subsequent query returns 404
        r404 = client.get(f"/api/gamification/summary?learner_id={lid}")
        assert r404.status_code == 404
