"""HTTP API (FastAPI) and static front-end hosting."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import Database
from .fraction_engine import AnswerParseError
from .gemini_service import GeminiService
from .interventions import build_intervention
from .misconceptions import PATTERNS
from .ml_hook import MLOpinion
from .questions import BANK, BANK_VERSION
from .service import EduMindService, ServiceError

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ID = r"^[0-9a-fA-F-]{36}$"


class AttemptIn(BaseModel):
    learner_id: str = Field(pattern=ID)
    question_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(max_length=32)


class FollowupIn(BaseModel):
    learner_id: str = Field(pattern=ID)
    followup_id: str = Field(min_length=1, max_length=40)
    option_id: str = Field(min_length=1, max_length=40)


class MasteryIn(BaseModel):
    learner_id: str = Field(pattern=ID)
    session_id: str = Field(pattern=ID)
    question_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(max_length=32)


class QuizAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(max_length=32)


class QuizGradeIn(BaseModel):
    answers: list[QuizAnswer] = Field(min_length=1, max_length=20)


class TutorChatMessage(BaseModel):
    role: str = Field(pattern=r"^(user|assistant|model)$")
    content: str = Field(min_length=1, max_length=2000)


class TutorChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    difficulty: str = Field(default="Intermediate", pattern=r"^(?i:beginner|intermediate|advanced)$")
    mode: str | None = Field(default=None, max_length=50)
    history: list[TutorChatMessage] = Field(default_factory=list, max_length=20)


def create_app(db_path: str | None = None, rate_limit_per_minute: int | None = None,
               model_path: str | None = None,
               gemini_api_key: str | None = None,
               gemini_model: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("EDUMIND_DB", "data/edumind.db")
    if rate_limit_per_minute is None:
        rate_limit_per_minute = int(os.environ.get("EDUMIND_RATE_LIMIT", "300"))

    app = FastAPI(title="EduMind AI", version="1.0.0",
                  description="Misconception Detective for middle-school fractions.")
    if model_path is None:
        model_path = os.environ.get("EDUMIND_MODEL", "models/misconception_clf.joblib")
    ml = MLOpinion.load_if_present(model_path) if model_path else None  # "" disables

    gemini_key = gemini_api_key if gemini_api_key is not None else os.environ.get("GEMINI_API_KEY", "")
    gemini_mdl = gemini_model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    gemini = GeminiService(api_key=gemini_key, model=gemini_mdl)
    app.state.gemini = gemini

    service = EduMindService(Database(db_path), ml=ml, gemini=gemini)
    app.state.service = service
    hits: dict[str, deque] = defaultdict(deque)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if rate_limit_per_minute > 0 and request.url.path.startswith("/api"):
            ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
            q = hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= rate_limit_per_minute:
                return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429)
            q.append(now)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; "
            "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(AnswerParseError)
    async def parse_error(_: Request, exc: AnswerParseError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # ---- API -----------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "questions": len(BANK), "bank_version": BANK_VERSION}

    @app.get("/api/skills")
    def skills():
        return service.skills()

    @app.post("/api/learners", status_code=201)
    def create_learner():
        return {"learner_id": service.create_learner()}

    @app.delete("/api/learners/{learner_id}")
    def delete_learner(learner_id: str):
        service.delete_learner(learner_id)
        return {"deleted": True}

    @app.get("/api/next-question")
    def next_question(learner_id: str = Query(pattern=ID), skill: str | None = None):
        return service.next_question(learner_id, skill)

    @app.post("/api/attempts")
    def attempts(body: AttemptIn):
        return service.submit_attempt(body.learner_id, body.question_id, body.answer)

    @app.post("/api/diagnostics/{session_id}/responses")
    def diagnostic_response(session_id: str, body: FollowupIn):
        if len(session_id) != 36:
            raise ServiceError("Bad session id.")
        return service.respond_followup(body.learner_id, session_id, body.followup_id, body.option_id)

    @app.get("/api/patterns/{pattern_id}/intervention")
    def pattern_intervention(pattern_id: str, question_id: str):
        if pattern_id not in PATTERNS:
            raise ServiceError("Unknown pattern.")
        from .questions import get_question
        q = get_question(question_id)
        if q is None or q.skill not in PATTERNS[pattern_id].skills:
            raise ServiceError("That pattern does not apply to this question.")
        return build_intervention(pattern_id, q)

    @app.post("/api/mastery-checks")
    def mastery(body: MasteryIn):
        return service.submit_mastery(body.learner_id, body.session_id, body.question_id, body.answer)

    @app.get("/api/progress")
    def progress(learner_id: str = Query(pattern=ID)):
        return service.progress(learner_id)

    @app.get("/api/quiz")
    def quiz(skill: str | None = None, n: int = Query(5, ge=1, le=10),
             difficulty: int | None = Query(None, ge=1, le=2), seed: int | None = None):
        return {"questions": service.quiz(skill, n, difficulty, seed)}

    @app.post("/api/quiz/grade")
    def quiz_grade(body: QuizGradeIn):
        return service.grade_quiz([a.model_dump() for a in body.answers])

    @app.get("/api/tutor/status")
    def tutor_status():
        return {
            "available": gemini.is_available(),
            "model": gemini.model,
            "provider": "google-genai",
        }

    @app.post("/api/tutor/chat")
    def tutor_chat(body: TutorChatIn):
        msg = body.message.strip()
        if not msg:
            raise ServiceError("Message cannot be empty.")
        return service.tutor_chat(
            message=msg,
            difficulty=body.difficulty.capitalize(),
            mode=body.mode,
            history=[m.model_dump() for m in body.history],
        )

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    return app
