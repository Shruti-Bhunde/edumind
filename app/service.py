"""Application logic: the full TRACE learning loop on top of the database."""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from .db import Database
from .diagnostic import (Decision, apply_followup, choose_followup, decide, followups_for,
                         initial_scores)
from .interventions import build_intervention, general_explanation, hint_for
from .misconceptions import PATTERNS, predictions
from .questions import SKILLS, BANK, Question, by_skill, check_answer, get_question

REVIEW_DELAY = timedelta(days=1)
CONFIDENCE_NOTE = ("A rule-based indicator of how consistent your answers were with one pattern. "
                   "It is not a calibrated probability, and it can be wrong.")
LEVEL_RANK = {"Not started": 0, "Practising": 1, "Getting there": 2, "Secure": 3}


class ServiceError(Exception):
    status = 400


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class BadRequest(ServiceError):
    status = 400


def level_from_recent(recent: list[int]) -> str:
    n = len(recent)
    if n == 0:
        return "Not started"
    c = sum(recent)
    if n >= 4 and c >= 4:
        return "Secure"
    if n >= 3 and c / n >= 0.6:
        return "Getting there"
    return "Practising"


class EduMindService:
    def __init__(self, db: Database, clock: Callable[[], datetime] | None = None, ml=None, gemini=None):
        self.db = db
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.ml = ml  # optional app.ml_hook.MLOpinion; never changes the decision
        self.gemini = gemini  # optional GeminiService; refines explanations, never changes decisions

    # ------------------------------------------------------------------ helpers
    def _now(self) -> datetime:
        return self._clock()

    def _iso(self, dt: datetime | None = None) -> str:
        return (dt or self._now()).isoformat()

    def _require_learner(self, learner_id: str) -> None:
        if not self.db.one("SELECT 1 FROM learners WHERE id=?", (learner_id,)):
            raise NotFound("Unknown learner. Start a new session.")

    def _session(self, session_id: str, learner_id: str):
        row = self.db.one("SELECT * FROM diagnostic_sessions WHERE id=? AND learner_id=?",
                          (session_id, learner_id))
        if not row:
            raise NotFound("Unknown diagnostic session.")
        return row

    def _question(self, question_id: str) -> Question:
        q = get_question(question_id)
        if not q:
            raise NotFound("Unknown question.")
        return q

    def _skill_recent(self, learner_id: str) -> dict[str, list[int]]:
        rows = self.db.all("SELECT skill, correct FROM attempts WHERE learner_id=? ORDER BY id",
                           (learner_id,))
        out: dict[str, list[int]] = {s: [] for s in SKILLS}
        for r in rows:
            out[r["skill"]].append(int(r["correct"]))
        return out

    # ----------------------------------------------------------------- learners
    def create_learner(self) -> str:
        lid = str(uuid.uuid4())
        self.db.execute("INSERT INTO learners(id, created_at) VALUES (?,?)", (lid, self._iso()))
        return lid

    def delete_learner(self, learner_id: str) -> None:
        self._require_learner(learner_id)
        with self.db.transaction():
            for table in ("mastery_checks", "diagnostic_sessions", "attempts", "review_queue"):
                self.db.execute(f"DELETE FROM {table} WHERE learner_id=?", (learner_id,))
            self.db.execute("DELETE FROM learners WHERE id=?", (learner_id,))

    # ---------------------------------------------------------------- questions
    def skills(self) -> list[dict]:
        return [{"id": k, "name": v["name"], "blurb": v["blurb"]} for k, v in SKILLS.items()]

    def next_question(self, learner_id: str, skill: str | None = None) -> dict:
        self._require_learner(learner_id)
        recent = self._skill_recent(learner_id)
        if skill is None:
            skill = self._pick_skill(learner_id, recent)
        elif skill not in SKILLS:
            raise BadRequest("Unknown skill.")
        seen = [r["question_id"] for r in self.db.all(
            "SELECT question_id FROM attempts WHERE learner_id=? AND skill=? AND kind='practice' "
            "ORDER BY id", (learner_id, skill))]
        pool = [q for q in by_skill(skill) if q.id not in set(seen)]
        if not pool:  # everything seen: revisit the least recently seen, never the last one
            order = {qid: i for i, qid in enumerate(seen)}
            last = seen[-1] if seen else None
            ranked = sorted(by_skill(skill), key=lambda q: order.get(q.id, -1))
            pool = [q for q in ranked if q.id != last][:3] or ranked
        rng = random.Random(f"{learner_id}:{skill}:{len(seen)}")
        q = rng.choice(pool)
        data = q.public()
        data["skill_level"] = level_from_recent(recent[skill][-5:])
        return data

    def _pick_skill(self, learner_id: str, recent: dict[str, list[int]]) -> str:
        due = self.db.all("SELECT skill FROM review_queue WHERE learner_id=? AND done=0 "
                          "AND due_at<=? ORDER BY due_at", (learner_id, self._iso()))
        if due:
            return due[0]["skill"]
        order = list(SKILLS)
        return min(order, key=lambda s: (LEVEL_RANK[level_from_recent(recent[s][-5:])],
                                         len(recent[s]), order.index(s)))

    def _pick_fresh(self, q: Question, exclude: set[str], seed: str) -> Question:
        pool = [x for x in by_skill(q.skill) if x.id not in exclude]
        pool = [x for x in pool if x.kind == q.kind] or pool or \
               [x for x in by_skill(q.skill) if x.id != q.id]
        return random.Random(seed).choice(pool)

    # ----------------------------------------------------------------- attempts
    def submit_attempt(self, learner_id: str, question_id: str, answer: str) -> dict:
        self._require_learner(learner_id)
        q = self._question(question_id)
        result = check_answer(q, answer)  # may raise AnswerParseError (not recorded)
        now = self._iso()
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT INTO attempts(learner_id, question_id, skill, submitted, correct, kind, created_at) "
                "VALUES (?,?,?,?,?,'practice',?)",
                (learner_id, question_id, q.skill, answer.strip(), int(result.correct), now))
            attempt_id = cur.lastrowid
            if result.correct:
                self.db.execute("UPDATE review_queue SET done=1 WHERE learner_id=? AND skill=? "
                                "AND done=0 AND due_at<=?", (learner_id, q.skill, now))
                recent = self._skill_recent(learner_id)[q.skill][-5:]
                return {"correct": True, "message": "Correct. Well done!", "note": result.note,
                        "skill_level": level_from_recent(recent)}

            scores, evidence = initial_scores(q, result.normalized)
            diagnosable = bool(predictions(q.skill, q.kind, q.payload, q.answer))
            first = choose_followup(q, scores, []) if diagnosable else None
            sid = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO diagnostic_sessions(id, attempt_id, learner_id, question_id, skill, "
                "scores, evidence, asked, status, mastery_questions, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (sid, attempt_id, learner_id, question_id, q.skill, json.dumps(scores),
                 json.dumps(evidence), "[]", "awaiting_followup", "[]", now))
        message = ("Not quite yet. Let's work out what happened. "
                   "Your thinking is the useful part here.")
        if first is None:
            # No misconception in the library predicts this wrong answer: don't guess.
            evidence = ["This answer isn't one of the specific patterns we look for, "
                        "so we won't guess at a cause. Here is the method again."]
            resolved = self._resolve(q, sid, scores, evidence, [],
                                     Decision("general", None, 0, 0.0, 0))
            return {"correct": False, "message": "Not quite yet. Let's go over it together.",
                    "session_id": sid, "resolved": resolved}
        return {"correct": False, "message": message, "session_id": sid, "followup": first.public()}

    # ------------------------------------------------------------- diagnostics
    def respond_followup(self, learner_id: str, session_id: str,
                         followup_id: str, option_id: str) -> dict:
        self._require_learner(learner_id)
        sess = self._session(session_id, learner_id)
        if sess["status"] != "awaiting_followup":
            raise Conflict("This diagnostic session is already finished.")
        q = self._question(sess["question_id"])
        scores = json.loads(sess["scores"])
        evidence = json.loads(sess["evidence"])
        asked = json.loads(sess["asked"])
        asked_ids = [a["followup_id"] for a in asked]

        expected = choose_followup(q, scores, asked_ids)
        if expected is None or expected.id != followup_id:
            raise Conflict("That follow-up question is not the one currently waiting.")
        option = next((o for o in expected.options if o.id == option_id), None)
        if option is None:
            raise BadRequest("Unknown option for this question.")

        apply_followup(scores, evidence, expected, option)
        asked.append({"followup_id": followup_id, "option_id": option_id})
        decision = decide(scores, len(asked))
        nxt = None
        if decision.status == "ask_more":
            nxt = choose_followup(q, scores, [a["followup_id"] for a in asked])
            if nxt is None:  # nothing left to ask: abstain
                decision = Decision("general", None, decision.top_score, decision.share, decision.margin)

        if decision.status == "ask_more":
            self.db.execute("UPDATE diagnostic_sessions SET scores=?, evidence=?, asked=? WHERE id=?",
                            (json.dumps(scores), json.dumps(evidence), json.dumps(asked), session_id))
            return {"status": "needs_followup", "followup": nxt.public()}

        return self._resolve(q, session_id, scores, evidence, asked, decision)

    def _resolve(self, q: Question, session_id: str, scores: dict, evidence: list,
                 asked: list, decision: Decision) -> dict:
        targeted = decision.status == "targeted"
        pattern_id = decision.pattern_id if targeted else None
        intervention = build_intervention(pattern_id, q) if targeted else general_explanation(q)
        mastery = self._pick_fresh(q, {q.id}, f"{session_id}:0")
        with self.db.transaction():
            self.db.execute(
                "UPDATE diagnostic_sessions SET scores=?, evidence=?, asked=?, status='resolved', "
                "selected_pattern=?, outcome=?, confidence_share=?, mastery_questions=?, resolved_at=? "
                "WHERE id=?",
                (json.dumps(scores), json.dumps(evidence), json.dumps(asked), pattern_id,
                 decision.status, decision.share, json.dumps([mastery.id]), self._iso(), session_id))

        if self.gemini and self.gemini.is_available():
            row = self.db.one("SELECT a.submitted FROM diagnostic_sessions s "
                              "JOIN attempts a ON a.id = s.attempt_id WHERE s.id=?", (session_id,))
            submitted = row["submitted"] if row else ""
            display_str = q.display.get("text", "") if isinstance(q.display, dict) else str(q.display or "")
            intervention = self.gemini.refine_explanation(
                question_text=q.prompt,
                display_text=display_str,
                student_answer=submitted,
                correct_answer=q.answer_text,
                diagnosis_status=decision.status,
                pattern_id=pattern_id,
                pattern_name=PATTERNS[pattern_id].name if targeted else None,
                evidence=evidence,
                existing_intervention=intervention,
            )

        return {
            "ml_opinion": self._ml_opinion(q, session_id, pattern_id),
            "status": "resolved",
            "outcome": decision.status,
            "pattern": ({"id": pattern_id, "name": PATTERNS[pattern_id].name} if targeted else None),
            "confidence_indicator": {
                "label": "Likely" if targeted else "Not sure",
                "score_share": decision.share,
                "note": CONFIDENCE_NOTE},
            "intervention": intervention,
            "evidence": evidence,
            "mastery_question": mastery.public(),
        }

    def _ml_opinion(self, q: Question, session_id: str, rule_pattern: str | None) -> dict | None:
        if self.ml is None:
            return None
        row = self.db.one("SELECT a.submitted FROM diagnostic_sessions s "
                          "JOIN attempts a ON a.id = s.attempt_id WHERE s.id=?", (session_id,))
        if not row:
            return None
        try:
            op = self.ml.opinion(q, row["submitted"])
        except Exception:  # the optional model must never break the learning loop
            return None
        if op is not None:
            op["agrees"] = op["pattern_id"] == rule_pattern
        return op

    # ----------------------------------------------------------- mastery checks
    def submit_mastery(self, learner_id: str, session_id: str, question_id: str, answer: str) -> dict:
        self._require_learner(learner_id)
        sess = self._session(session_id, learner_id)
        if sess["status"] != "resolved":
            raise Conflict("Finish the diagnostic questions first.")
        if sess["mastery_result"] is not None:
            raise Conflict("This check is already finished.")
        issued = json.loads(sess["mastery_questions"])
        if question_id not in issued:
            raise BadRequest("That question was not issued for this check.")
        if self.db.one("SELECT 1 FROM mastery_checks WHERE session_id=? AND question_id=?",
                       (session_id, question_id)):
            raise Conflict("You already answered that question.")
        q = self._question(question_id)
        original = self._question(sess["question_id"])
        result = check_answer(q, answer)  # may raise AnswerParseError
        now = self._iso()
        pattern_id = sess["selected_pattern"]

        with self.db.transaction():
            self.db.execute(
                "INSERT INTO attempts(learner_id, question_id, skill, submitted, correct, kind, created_at) "
                "VALUES (?,?,?,?,?,'mastery',?)",
                (learner_id, question_id, q.skill, answer.strip(), int(result.correct), now))
            self.db.execute(
                "INSERT INTO mastery_checks(session_id, learner_id, question_id, submitted, correct, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, learner_id, question_id, answer.strip(), int(result.correct), now))
            if result.correct:
                self.db.execute("UPDATE diagnostic_sessions SET mastery_result='passed' WHERE id=?",
                                (session_id,))
                return {"correct": True, "outcome": "mastered", "note": result.note,
                        "message": "You solved a brand-new question using the method. "
                                   "That is how we know it is sinking in."}
            failures = self.db.one("SELECT COUNT(*) n FROM mastery_checks WHERE session_id=? AND correct=0",
                                   (session_id,))["n"]
            if failures < 2:
                fresh = self._pick_fresh(q, set(issued) | {original.id}, f"{session_id}:{failures}")
                issued.append(fresh.id)
                self.db.execute("UPDATE diagnostic_sessions SET mastery_questions=? WHERE id=?",
                                (json.dumps(issued), session_id))
                return {"correct": False, "outcome": "retry",
                        "message": "Not yet, and that's fine. Try another one.",
                        "hint": hint_for(pattern_id, q), "mastery_question": fresh.public()}
            self.db.execute("UPDATE diagnostic_sessions SET mastery_result='failed' WHERE id=?",
                            (session_id,))
            self.db.execute("INSERT INTO review_queue(learner_id, skill, due_at, reason) VALUES (?,?,?,?)",
                            (learner_id, q.skill, self._iso(self._now() + REVIEW_DELAY),
                             "Needs another look after a difficult check"))
        return {"correct": False, "outcome": "review_scheduled",
                "message": "This topic is still tricky. We'll bring it back tomorrow, and it may "
                           "help to go through it with a teacher.",
                "answer": q.answer_text, "hint": hint_for(pattern_id, q)}

    # --------------------------------------------------------------- progress
    def progress(self, learner_id: str) -> dict:
        self._require_learner(learner_id)
        recent = self._skill_recent(learner_id)
        totals = self.db.all("SELECT skill, COUNT(*) n, SUM(correct) c FROM attempts "
                             "WHERE learner_id=? GROUP BY skill", (learner_id,))
        tmap = {r["skill"]: (r["n"], int(r["c"] or 0)) for r in totals}
        skills = []
        for sid, info in SKILLS.items():
            n, c = tmap.get(sid, (0, 0))
            skills.append({"skill": sid, "name": info["name"], "attempts": n, "correct": c,
                           "level": level_from_recent(recent[sid][-5:]),
                           "recent": recent[sid][-5:]})
        miscs = self.db.all(
            "SELECT selected_pattern p, COUNT(*) n, SUM(mastery_result='passed') passed, "
            "MAX(created_at) last FROM diagnostic_sessions "
            "WHERE learner_id=? AND selected_pattern IS NOT NULL GROUP BY selected_pattern ORDER BY n DESC",
            (learner_id,))
        now = self._iso()
        reviews = self.db.all("SELECT skill, due_at, reason FROM review_queue "
                              "WHERE learner_id=? AND done=0 ORDER BY due_at", (learner_id,))
        return {
            "skills": skills,
            "misconceptions": [{"pattern_id": r["p"], "name": PATTERNS[r["p"]].name, "times_seen": r["n"],
                                "resolved": int(r["passed"] or 0), "last_seen": r["last"]} for r in miscs],
            "review": [{"skill": r["skill"], "name": SKILLS[r["skill"]]["name"], "due_at": r["due_at"],
                        "due": r["due_at"] <= now, "reason": r["reason"]} for r in reviews],
            "totals": {"attempts": sum(a for a, _ in tmap.values()),
                       "correct": sum(c for _, c in tmap.values())},
        }

    # ------------------------------------------------------------------ quiz
    def quiz(self, skill: str | None, n: int, difficulty: int | None, seed: int | None = None) -> list[dict]:
        if skill is not None and skill not in SKILLS:
            raise BadRequest("Unknown skill.")
        pool = [q for q in (by_skill(skill) if skill else BANK)
                if difficulty is None or q.difficulty == difficulty]
        if not pool:
            raise BadRequest("No questions match those filters.")
        rng = random.Random(seed)
        return [q.public() for q in rng.sample(pool, min(n, len(pool)))]

    def grade_quiz(self, answers: list[dict]) -> dict:
        from .fraction_engine import AnswerParseError
        results, score = [], 0
        for item in answers:
            q = self._question(item["question_id"])
            try:
                res = check_answer(q, item["answer"])
                ok, msg = res.correct, (res.note or "")
            except AnswerParseError as e:
                ok, msg = False, str(e)
            score += int(ok)
            results.append({"question_id": q.id, "correct": ok, "answer": q.answer_text, "message": msg})
        return {"score": score, "total": len(results), "results": results}

    # ------------------------------------------------------------------ tutor
    def tutor_chat(self, message: str, difficulty: str = "Intermediate",
                   mode: str | None = None, history: list[dict] | None = None) -> dict:
        if not self.gemini or not self.gemini.is_available():
            raise ServiceError("EduMind AI Tutor requires GEMINI_API_KEY to be configured on the server.")
        try:
            reply = self.gemini.tutor_chat(message=message, difficulty=difficulty, mode=mode, history=history)
            return {"reply": reply, "difficulty": difficulty, "mode": mode or "chat"}
        except RuntimeError as e:
            raise ServiceError(str(e))

