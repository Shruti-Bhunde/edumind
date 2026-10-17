"""Application logic: the full TRACE learning loop on top of the database."""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .db import Database
from .diagnostic import (Decision, apply_followup, choose_followup, decide, followups_for,
                         initial_scores)
from .gamification import GamificationEngine
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
        self.gamification = GamificationEngine(self.db)

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
            for table in (
                "mastery_checks", "diagnostic_sessions", "attempts", "review_queue",
                "gamification_summaries", "activity_dates", "earned_badges",
                "quiz_completions", "daily_quests"
            ):
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
                newly_unlocked = self.gamification.process_mastery_success(learner_id, session_id, self._now())
                return {"correct": True, "outcome": "mastered", "note": result.note,
                        "newly_unlocked_badges": newly_unlocked,
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

    def grade_quiz(self, answers: list[dict], learner_id: str | None = None,
                   idempotency_key: str | None = None) -> dict:
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

        out: dict[str, Any] = {"score": score, "total": len(results), "results": results}
        if learner_id:
            if self.db.one("SELECT 1 FROM learners WHERE id=?", (learner_id,)):
                now = self._now()
                now_iso = self._iso(now)
                is_duplicate = False
                if idempotency_key:
                    is_duplicate = bool(self.db.one("SELECT 1 FROM quiz_completions WHERE id=?", (idempotency_key,)))
                if not is_duplicate:
                    with self.db.transaction():
                        for item, res in zip(answers, results):
                            q = self._question(item["question_id"])
                            self.db.execute(
                                "INSERT INTO attempts(learner_id, question_id, skill, submitted, correct, kind, created_at) "
                                "VALUES (?,?,?,?,?,'quiz',?)",
                                (learner_id, q.id, q.skill, item["answer"].strip(), int(res["correct"]), now_iso)
                            )
                gam = self.gamification.process_quiz_completion(
                    learner_id=learner_id,
                    score=score,
                    total=len(results),
                    idempotency_key=idempotency_key,
                    now=now,
                )
                out["gamification"] = gam
        return out

    # ----------------------------------------------------------- gamification
    def get_gamification_summary(self, learner_id: str) -> dict:
        self._require_learner(learner_id)
        return self.gamification.get_summary(learner_id, self._now())

    def get_daily_quests(self, learner_id: str) -> list[dict]:
        self._require_learner(learner_id)
        return self.gamification.get_daily_quests(learner_id, self._now())

    # ------------------------------------------------------------- analytics
    def progress_analytics(self, learner_id: str, days: int = 7) -> dict:
        self._require_learner(learner_id)
        if days not in (7, 30, 90):
            days = 7

        now = self._now()
        end_date = now.date()
        start_date = end_date - timedelta(days=days - 1)
        start_iso = f"{start_date.isoformat()}T00:00:00"

        # Query all attempts for this learner in the selected date range
        rows = self.db.all(
            "SELECT id, correct, created_at FROM attempts WHERE learner_id=? AND created_at>=? ORDER BY created_at ASC",
            (learner_id, start_iso)
        )

        daily_counts: dict[str, list[int]] = {}
        weekday_counts: list[list[int]] = [[] for _ in range(7)]

        for r in rows:
            created_str = r["created_at"]
            d_str = created_str[:10]
            try:
                dt = datetime.fromisoformat(created_str)
            except Exception:
                dt = datetime.strptime(d_str, "%Y-%m-%d")

            c = int(r["correct"])
            if d_str not in daily_counts:
                daily_counts[d_str] = []
            daily_counts[d_str].append(c)
            weekday_counts[dt.weekday()].append(c)

        timeline = []
        total_attempts = 0
        total_correct = 0
        active_days = 0

        for i in range(days):
            d = start_date + timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            atts = daily_counts.get(d_str, [])
            n_atts = len(atts)
            n_corr = sum(atts)
            has_act = n_atts > 0

            if has_act:
                active_days += 1
                total_attempts += n_atts
                total_correct += n_corr
                acc = round((n_corr / n_atts) * 100, 1)
            else:
                acc = None

            label = d.strftime("%b %d") if days <= 30 else d.strftime("%m/%d")
            timeline.append({
                "date": d_str,
                "label": label,
                "day_name": d.strftime("%a"),
                "attempts": n_atts,
                "correct": n_corr,
                "accuracy": acc,
                "has_activity": has_act,
            })

        weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        weekday_shorts = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        weekdays = []
        max_weekday_attempts = 0

        for idx in range(7):
            w_atts = weekday_counts[idx]
            n_w_atts = len(w_atts)
            n_w_corr = sum(w_atts)
            w_acc = round((n_w_corr / n_w_atts) * 100, 1) if n_w_atts > 0 else None
            if n_w_atts > max_weekday_attempts:
                max_weekday_attempts = n_w_atts

            weekdays.append({
                "weekday_index": idx,
                "day_name": weekday_names[idx],
                "short_name": weekday_shorts[idx],
                "attempts": n_w_atts,
                "correct": n_w_corr,
                "accuracy": w_acc,
                "is_max": False,
            })

        for w in weekdays:
            w["is_max"] = (w["attempts"] == max_weekday_attempts and max_weekday_attempts > 0)

        avg_accuracy = round((total_correct / total_attempts) * 100, 1) if total_attempts > 0 else None

        gam_summary = self.gamification.get_summary(learner_id, now)
        current_streak = gam_summary["streak"]["current"]
        longest_streak = gam_summary["streak"]["longest"]

        if total_attempts > 0:
            acc_int = int(round(avg_accuracy or 0))
            insight_text = (
                f"You attempted {total_attempts} question{'s' if total_attempts != 1 else ''} "
                f"in the last {days} days with {acc_int}% accuracy across {active_days} active study day{'s' if active_days != 1 else ''}."
            )
        else:
            insight_text = "No questions attempted in this time range yet. Complete questions or quizzes to start seeing your progress trends!"

        return {
            "days": days,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "summary": {
                "total_attempts": total_attempts,
                "total_correct": total_correct,
                "average_accuracy": avg_accuracy,
                "active_days": active_days,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                "insight_text": insight_text,
            },
            "timeline": timeline,
            "weekdays": weekdays,
        }

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

