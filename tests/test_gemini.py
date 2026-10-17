"""Unit and integration tests for Gemini explanation refinement and AI Tutor chatbot."""
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.gemini_service import GeminiService
from app.main import create_app
from app.questions import by_skill


def test_gemini_service_offline_fallback():
    service = GeminiService(api_key="", model="gemini-2.5-flash")
    assert service.is_available() is False

    original_iv = {
        "title": "Adding fractions",
        "why": "The denominators were added directly.",
        "steps": ["Find common denominator.", "Add numerators."],
        "bars": [{"label": "1/2", "parts": 2, "shaded": 1}],
        "hint": "Match bottom numbers.",
    }

    # Refine should gracefully return fallback
    refined = service.refine_explanation(
        question_text="1/2 + 1/3",
        display_text="1/2 + 1/3",
        student_answer="2/5",
        correct_answer="5/6",
        diagnosis_status="targeted",
        pattern_id="M01",
        pattern_name="Add numerators and denominators",
        evidence=["Added denominators separately"],
        existing_intervention=original_iv,
        difficulty="Intermediate",
    )
    assert refined["gemini_refined"] is False
    assert refined["why"] == original_iv["why"]
    assert refined["steps"] == original_iv["steps"]

    # Tutor chat should raise RuntimeError when unconfigured
    with pytest.raises(RuntimeError, match="not currently configured"):
        service.tutor_chat(message="What is a fraction?")


def test_gemini_service_mocked_refinement():
    service = GeminiService(api_key="fake-test-key", model="gemini-2.5-flash")
    assert service.is_available() is True

    original_iv = {
        "title": "Adding fractions",
        "why": "The bottom numbers were added together.",
        "steps": ["Find common denominator.", "Add numerators."],
        "bars": [],
        "hint": "Match bottom numbers.",
    }

    mock_response = MagicMock()
    mock_response.text = (
        "WHY: You added the top and bottom numbers directly instead of finding a common unit. "
        "Halves and thirds describe different piece sizes, so you must find common pieces first.\n"
        "STEPS:\n"
        "1. Find a shared common denominator for both fractions.\n"
        "2. Convert each fraction to that common denominator.\n"
        "3. Add only the numerators together and keep the denominator."
    )

    with patch.object(service._client.models, "generate_content", return_value=mock_response):
        refined = service.refine_explanation(
            question_text="1/2 + 1/3",
            display_text="1/2 + 1/3",
            student_answer="2/5",
            correct_answer="5/6",
            diagnosis_status="targeted",
            pattern_id="M01",
            pattern_name="Add numerators and denominators",
            evidence=["Added denominators separately"],
            existing_intervention=original_iv,
            difficulty="Intermediate",
        )

        assert refined["gemini_refined"] is True
        assert refined["original_why"] == original_iv["why"]
        assert "common unit" in refined["why"]
        assert len(refined["steps"]) == 3
        assert "shared common denominator" in refined["steps"][0]


def test_gemini_service_refinement_error_fallback():
    service = GeminiService(api_key="fake-test-key", model="gemini-2.5-flash")
    original_iv = {
        "title": "Adding fractions",
        "why": "Original why",
        "steps": ["Step 1", "Step 2"],
    }

    # Force an exception inside generate_content
    with patch.object(service._client.models, "generate_content", side_effect=Exception("API Timeout")):
        refined = service.refine_explanation(
            question_text="1/2 + 1/3",
            display_text="",
            student_answer="2/5",
            correct_answer="5/6",
            diagnosis_status="targeted",
            pattern_id="M01",
            pattern_name="Add numerators and denominators",
            evidence=[],
            existing_intervention=original_iv,
        )
        assert refined["gemini_refined"] is False
        assert refined["why"] == "Original why"


def test_tutor_chat_strips_trailing_questions():
    service = GeminiService(api_key="fake-test-key", model="gemini-2.5-flash")

    # In the mentoring round, a strict rule was set:
    # "Do not automatically append follow-up questions to the end of the tutor's responses."
    mock_response = MagicMock()
    mock_response.text = (
        "The value of tan(45°) is 1. In a right-angled isosceles triangle with 45-degree angles, "
        "the opposite side and adjacent side have identical lengths. Therefore, dividing them gives 1.\n\n"
        "Before we move on, let me ask you a question: What do you think the value of sin(45°) is?"
    )

    with patch.object(service._client.models, "generate_content", return_value=mock_response):
        reply = service.tutor_chat(
            message="what is value of tan 45",
            difficulty="Intermediate",
            mode="chat",
        )
        assert "The value of tan(45°) is 1" in reply
        # Verify trailing question was stripped
        assert "Before we move on" not in reply
        assert "What do you think" not in reply


def test_api_tutor_status_endpoint():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="")
    with TestClient(app) as client:
        r = client.get("/api/tutor/status")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False
        assert data["provider"] == "google-genai"


def test_api_tutor_chat_offline_and_validation():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="")
    with TestClient(app) as client:
        # Validation: empty message
        r = client.post("/api/tutor/chat", json={"message": "   "})
        assert r.status_code == 400

        # Offline message
        r = client.post("/api/tutor/chat", json={"message": "Help me with algebra"})
        assert r.status_code == 400
        assert "GEMINI_API_KEY" in r.json()["detail"]


def test_api_tutor_chat_with_mock():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="fake-key")
    gemini: GeminiService = app.state.gemini

    mock_response = MagicMock()
    mock_response.text = "In mathematics, a prime number is a natural number greater than 1 with no positive divisors other than 1 and itself."

    with patch.object(gemini._client.models, "generate_content", return_value=mock_response):
        with TestClient(app) as client:
            r = client.post("/api/tutor/chat", json={
                "message": "What is a prime number?",
                "difficulty": "Intermediate",
                "mode": "chat",
                "history": [],
            })
            assert r.status_code == 200
            data = r.json()
            assert "prime number is a natural number" in data["reply"]
            assert data["difficulty"] == "Intermediate"
            assert data["mode"] == "chat"


def test_api_trace_loop_with_gemini_refinement():
    app = create_app(":memory:", rate_limit_per_minute=0, model_path="", gemini_api_key="fake-key")
    gemini: GeminiService = app.state.gemini

    mock_response = MagicMock()
    mock_response.text = (
        "WHY: You added the denominators together. Fractions can only be combined once they represent pieces of identical size.\n"
        "STEPS:\n"
        "1. Find a common denominator.\n"
        "2. Adjust numerators.\n"
        "3. Add the numerators."
    )

    with patch.object(gemini._client.models, "generate_content", return_value=mock_response):
        with TestClient(app) as client:
            lid = client.post("/api/learners").json()["learner_id"]
            q = by_skill("add")[0]
            from app.misconceptions import predictions
            from app.fraction_engine import fmt
            p = next(p for p in predictions(q.skill, q.kind, q.payload, q.answer) if p.pattern_id == "M01")
            ans = p.typed if p.typed is not None else (fmt(p.value) if not isinstance(p.value, (int, str)) else str(p.value))

            # Submit wrong answer (adding numerators and denominators)
            att = client.post("/api/attempts", json={"learner_id": lid, "question_id": q.id, "answer": ans}).json()
            sid = att["session_id"]
            fu = att["followup"]

            # Respond to follow-up
            res = client.post(f"/api/diagnostics/{sid}/responses", json={
                "learner_id": lid,
                "followup_id": fu["id"],
                "option_id": "sum",
            }).json()

            assert res["status"] == "resolved"
            assert res["outcome"] == "targeted"
            assert res["intervention"]["gemini_refined"] is True
            assert "identical size" in res["intervention"]["why"]
            assert len(res["intervention"]["steps"]) == 3
            assert res["intervention"]["bars"]  # existing bars preserved
            assert res["mastery_question"]  # mastery question preserved
