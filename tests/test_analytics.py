"""Unit and API tests for Individual Learning Progress Analytics and Graphs."""
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.service import EduMindService
from app.questions import get_question


def make_service(clock=None):
    db = Database(":memory:")
    return EduMindService(db, clock=clock)


def test_progress_analytics_empty_learner():
    day = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    service = make_service(clock=lambda: day)
    lid = service.create_learner()

    res = service.progress_analytics(lid, days=7)
    assert res["days"] == 7
    assert res["summary"]["total_attempts"] == 0
    assert res["summary"]["total_correct"] == 0
    assert res["summary"]["average_accuracy"] is None
    assert res["summary"]["active_days"] == 0
    assert res["summary"]["current_streak"] == 0

    assert len(res["timeline"]) == 7
    for pt in res["timeline"]:
        assert pt["attempts"] == 0
        assert pt["correct"] == 0
        assert pt["accuracy"] is None
        assert pt["has_activity"] is False

    assert len(res["weekdays"]) == 7
    # Verify strict Monday-to-Sunday ordering
    expected_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert [w["day_name"] for w in res["weekdays"]] == expected_days
    for w in res["weekdays"]:
        assert w["attempts"] == 0
        assert w["accuracy"] is None
        assert w["is_max"] is False


def test_progress_analytics_with_attempts_and_filters():
    base_dt = datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc)
    current_time = base_dt

    def clock():
        return current_time

    service = make_service(clock=clock)
    lid = service.create_learner()

    # Day 0: 2 attempts on Oct 10 (Saturday) -> 1 correct, 1 wrong -> 50% accuracy
    current_time = base_dt
    q = service.quiz(n=2, skill="add", difficulty=1, seed=10)
    q_corr = get_question(q[0]["id"])
    q_wrong = get_question(q[1]["id"])
    service.submit_attempt(lid, q_corr.id, q_corr.answer_text)  # correct
    service.submit_attempt(lid, q_wrong.id, "999/999")          # wrong

    # Day 1: 0 attempts on Oct 11 (Sunday) -> zero-attempt day

    # Day 2: 3 attempts on Oct 12 (Monday) -> 3 correct -> 100% accuracy
    current_time = base_dt + timedelta(days=2)
    for q_item in service.quiz(n=3, skill="add", difficulty=1, seed=12):
        q_obj = get_question(q_item["id"])
        service.submit_attempt(lid, q_obj.id, q_obj.answer_text)

    # Fast forward to Oct 16 (Friday) and inspect 7-day analytics
    current_time = base_dt + timedelta(days=6)  # Oct 16
    res = service.progress_analytics(lid, days=7)

    assert res["summary"]["total_attempts"] == 5
    assert res["summary"]["total_correct"] == 4
    # (4 / 5) * 100 = 80.0%
    assert res["summary"]["average_accuracy"] == 80.0
    assert res["summary"]["active_days"] == 2

    # Check timeline
    day_map = {pt["date"]: pt for pt in res["timeline"]}
    assert "2026-10-10" in day_map
    assert "2026-10-11" in day_map
    assert "2026-10-12" in day_map

    # Oct 10: 2 attempts, 1 correct, 50% accuracy
    assert day_map["2026-10-10"]["attempts"] == 2
    assert day_map["2026-10-10"]["correct"] == 1
    assert day_map["2026-10-10"]["accuracy"] == 50.0
    assert day_map["2026-10-10"]["has_activity"] is True

    # Oct 11: 0 attempts -> accuracy MUST BE None (not 0%!)
    assert day_map["2026-10-11"]["attempts"] == 0
    assert day_map["2026-10-11"]["correct"] == 0
    assert day_map["2026-10-11"]["accuracy"] is None
    assert day_map["2026-10-11"]["has_activity"] is False

    # Oct 12: 3 attempts, 3 correct, 100% accuracy
    assert day_map["2026-10-12"]["attempts"] == 3
    assert day_map["2026-10-12"]["correct"] == 3
    assert day_map["2026-10-12"]["accuracy"] == 100.0
    assert day_map["2026-10-12"]["has_activity"] is True

    # Check weekdays
    w_map = {w["short_name"]: w for w in res["weekdays"]}
    assert w_map["Mon"]["attempts"] == 3
    assert w_map["Mon"]["accuracy"] == 100.0
    assert w_map["Mon"]["is_max"] is True  # Monday was the most active day (3 attempts vs 2 on Sat)
    assert w_map["Sat"]["attempts"] == 2
    assert w_map["Sat"]["is_max"] is False
    assert w_map["Sun"]["attempts"] == 0

    # Test 30-day filter
    res_30 = service.progress_analytics(lid, days=30)
    assert len(res_30["timeline"]) == 30
    assert res_30["summary"]["total_attempts"] == 5

    # Test 90-day filter
    res_90 = service.progress_analytics(lid, days=90)
    assert len(res_90["timeline"]) == 90
    assert res_90["summary"]["total_attempts"] == 5


def test_progress_analytics_learner_isolation():
    service = make_service()
    lid1 = service.create_learner()
    lid2 = service.create_learner()

    q = service.quiz(skill="add", n=1, difficulty=1, seed=1)[0]
    q_obj = get_question(q["id"])
    service.submit_attempt(lid1, q_obj.id, q_obj.answer_text)

    # Learner 1 has 1 attempt
    a1 = service.progress_analytics(lid1, days=7)
    assert a1["summary"]["total_attempts"] == 1

    # Learner 2 has 0 attempts (isolated)
    a2 = service.progress_analytics(lid2, days=7)
    assert a2["summary"]["total_attempts"] == 0


def test_api_progress_analytics_endpoint():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="")
    with TestClient(app) as client:
        lid = client.post("/api/learners").json()["learner_id"]

        r = client.get(f"/api/progress/analytics?learner_id={lid}&days=7")
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == 7
        assert "timeline" in data
        assert "weekdays" in data
        assert "summary" in data
        assert len(data["timeline"]) == 7
        assert len(data["weekdays"]) == 7
