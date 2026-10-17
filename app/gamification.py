"""EduMind AI Gamification Engine.

Provides achievement badges, daily learning streaks, and daily quests with rewards.
Preserves existing learning workflow and ensures data integrity and idempotency.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database


@dataclass(frozen=True)
class BadgeDefinition:
    id: str
    name: str
    description: str
    icon: str
    unlock_condition: str
    category: str  # "quiz", "streak", "mastery", "quest"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "unlock_condition": self.unlock_condition,
            "category": self.category,
        }


BADGE_CATALOG: dict[str, BadgeDefinition] = {
    "first_steps": BadgeDefinition(
        id="first_steps",
        name="First Steps",
        description="Successfully complete your first quiz",
        icon="🌱",
        unlock_condition="Successfully complete 1 quiz",
        category="quiz",
    ),
    "perfect_score": BadgeDefinition(
        id="perfect_score",
        name="Perfect Score",
        description="Complete a quiz with every question correct",
        icon="🌟",
        unlock_condition="Score 100% on any quiz with 3 or more questions",
        category="quiz",
    ),
    "quiz_champion": BadgeDefinition(
        id="quiz_champion",
        name="Quiz Champion",
        description="Successfully complete 5 quizzes",
        icon="🏆",
        unlock_condition="Successfully complete 5 quizzes",
        category="quiz",
    ),
    "consistent_learner": BadgeDefinition(
        id="consistent_learner",
        name="Consistent Learner",
        description="Maintain a 3-day learning streak",
        icon="🔥",
        unlock_condition="Learn on 3 consecutive calendar days",
        category="streak",
    ),
    "week_warrior": BadgeDefinition(
        id="week_warrior",
        name="Week Warrior",
        description="Maintain a 7-day learning streak",
        icon="⚡",
        unlock_condition="Learn on 7 consecutive calendar days",
        category="streak",
    ),
    "daily_quest_hero": BadgeDefinition(
        id="daily_quest_hero",
        name="Daily Quest Hero",
        description="Complete any daily learning quest",
        icon="🎯",
        unlock_condition="Complete at least one daily quest",
        category="quest",
    ),
    "mastery_achieved": BadgeDefinition(
        id="mastery_achieved",
        name="Mastery Achieved",
        description="Successfully pass a fresh question mastery check",
        icon="🎓",
        unlock_condition="Pass a diagnostic mastery check",
        category="mastery",
    ),
    "fraction_explorer": BadgeDefinition(
        id="fraction_explorer",
        name="Fraction Explorer",
        description="Complete today's designated fractions challenge",
        icon="🧭",
        unlock_condition="Complete the Fraction Explorer daily quest",
        category="quest",
    ),
    "accuracy_star": BadgeDefinition(
        id="accuracy_star",
        name="Accuracy Star",
        description="Complete a quiz with at least 80% accuracy",
        icon="⭐",
        unlock_condition="Complete the Accuracy Challenge daily quest",
        category="quest",
    ),
    "daily_practice": BadgeDefinition(
        id="daily_practice",
        name="Daily Practice",
        description="Successfully complete two learning quizzes in a single day",
        icon="📚",
        unlock_condition="Complete the Daily Practice quest",
        category="quest",
    ),
}


DAILY_QUEST_DEFINITIONS = [
    {
        "quest_id": "quest_fraction_explorer",
        "title": "Fraction Explorer",
        "description": "Complete today's designated fractions quiz.",
        "objective": "Complete 1 quiz today",
        "target": 1,
        "reward_badge_id": "fraction_explorer",
    },
    {
        "quest_id": "quest_accuracy",
        "title": "Accuracy Challenge",
        "description": "Demonstrate high accuracy on your fractions quiz.",
        "objective": "Complete a quiz with at least 80% accuracy",
        "target": 1,
        "reward_badge_id": "accuracy_star",
    },
    {
        "quest_id": "quest_daily_practice",
        "title": "Daily Practice",
        "description": "Keep your skills sharp with consistent practice.",
        "objective": "Complete 2 quizzes today",
        "target": 2,
        "reward_badge_id": "daily_practice",
    },
]


def streak_motivational_message(streak: int, practiced_today: bool) -> str:
    if streak == 0:
        return "Complete a quiz to start your daily learning streak!"
    if streak == 1:
        return "🔥 1-day streak: You've started strong! Come back tomorrow to keep the flame alive."
    if streak == 2:
        return "🔥 2-day streak: 2 days in a row! Momentum is building."
    if streak == 3:
        return "🔥 3-day streak: Consistent Learner! You're establishing a solid habit."
    if 4 <= streak <= 6:
        return f"🔥 {streak}-day streak: {streak} days in a row! Practice makes permanent."
    if streak == 7:
        return "⚡ 7-day streak: Week Warrior! A full week of consistent learning mastery!"
    return f"🔥 {streak}-day streak: Unstoppable! {streak} consecutive days of learning."


class GamificationEngine:
    """Manages streaks, badges, quests, and rewards with full idempotency."""

    def __init__(self, db: Database):
        self.db = db

    def get_summary(self, learner_id: str, now: datetime | None = None) -> dict[str, Any]:
        """Returns the full gamification profile for a learner."""
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        row = self.db.one("SELECT * FROM gamification_summaries WHERE learner_id=?", (learner_id,))
        if not row:
            current_streak = 0
            longest_streak = 0
            last_activity_date = None
            quizzes_completed = 0
            perfect_quizzes = 0
            quests_completed = 0
            mastery_passed = 0
        else:
            current_streak = int(row["current_streak"])
            longest_streak = int(row["longest_streak"])
            last_activity_date = row["last_activity_date"]
            quizzes_completed = int(row["quizzes_completed"])
            perfect_quizzes = int(row["perfect_quizzes"])
            quests_completed = int(row["quests_completed"])
            mastery_passed = int(row["mastery_passed"])

            # If more than 1 day has elapsed without activity and student hasn't practiced today,
            # active display streak drops to 0 until next activity
            if last_activity_date and last_activity_date < yesterday:
                current_streak = 0

        practiced_today = bool(last_activity_date == today)

        # Weekly activity calendar (past 7 days including today)
        weekly_days = []
        for i in range(6, -1, -1):
            day_dt = now - timedelta(days=i)
            day_str = day_dt.strftime("%Y-%m-%d")
            active_row = self.db.one(
                "SELECT 1 FROM activity_dates WHERE learner_id=? AND activity_date=?",
                (learner_id, day_str)
            )
            weekly_days.append({
                "date": day_str,
                "day_name": day_dt.strftime("%a"),
                "active": bool(active_row),
                "is_today": bool(day_str == today),
            })

        # Earned badges
        earned_rows = self.db.all(
            "SELECT badge_id, awarded_at, reason FROM earned_badges WHERE learner_id=? ORDER BY awarded_at ASC",
            (learner_id,)
        )
        earned_map = {r["badge_id"]: {"awarded_at": r["awarded_at"], "reason": r["reason"]} for r in earned_rows}

        badges_list = []
        for bid, bdef in BADGE_CATALOG.items():
            b_info = bdef.to_dict()
            if bid in earned_map:
                b_info["earned"] = True
                b_info["awarded_at"] = earned_map[bid]["awarded_at"]
                b_info["reason"] = earned_map[bid]["reason"]
            else:
                b_info["earned"] = False
                b_info["awarded_at"] = None
                b_info["reason"] = None
            badges_list.append(b_info)

        # Daily quests for today
        quests = self.get_daily_quests(learner_id, now)

        return {
            "streak": {
                "current": current_streak,
                "longest": longest_streak,
                "last_activity_date": last_activity_date,
                "practiced_today": practiced_today,
                "message": streak_motivational_message(current_streak, practiced_today),
                "weekly_calendar": weekly_days,
            },
            "badges": badges_list,
            "earned_count": len(earned_rows),
            "total_badges": len(BADGE_CATALOG),
            "quests": quests,
            "stats": {
                "quizzes_completed": quizzes_completed,
                "perfect_quizzes": perfect_quizzes,
                "quests_completed": quests_completed,
                "mastery_passed": mastery_passed,
            },
        }

    def get_daily_quests(self, learner_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Retrieves or initializes stable daily quests for today."""
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        with self.db.transaction():
            for qdef in DAILY_QUEST_DEFINITIONS:
                qid = qdef["quest_id"]
                pk = f"{learner_id}:{today}:{qid}"
                existing = self.db.one("SELECT 1 FROM daily_quests WHERE id=?", (pk,))
                if not existing:
                    self.db.execute(
                        "INSERT INTO daily_quests(id, learner_id, quest_id, date, progress, target, completed, reward_claimed, reward_badge_id) "
                        "VALUES (?,?,?,?,0,?,0,0,?)",
                        (pk, learner_id, qid, today, qdef["target"], qdef["reward_badge_id"])
                    )

        rows = self.db.all(
            "SELECT * FROM daily_quests WHERE learner_id=? AND date=? ORDER BY quest_id",
            (learner_id, today)
        )
        row_map = {r["quest_id"]: r for r in rows}

        results = []
        for qdef in DAILY_QUEST_DEFINITIONS:
            qid = qdef["quest_id"]
            row = row_map.get(qid)
            progress = int(row["progress"]) if row else 0
            target = qdef["target"]
            completed = bool(row["completed"]) if row else False
            reward_claimed = bool(row["reward_claimed"]) if row else False

            status = "Completed" if completed else ("In Progress" if progress > 0 else "Not Started")
            reward_badge = BADGE_CATALOG.get(qdef["reward_badge_id"])

            results.append({
                "quest_id": qid,
                "title": qdef["title"],
                "description": qdef["description"],
                "objective": qdef["objective"],
                "progress": min(progress, target),
                "target": target,
                "status": status,
                "completed": completed,
                "reward_claimed": reward_claimed,
                "reward_badge": reward_badge.to_dict() if reward_badge else None,
            })
        return results

    def record_activity_and_update_streak(
        self, learner_id: str, activity_type: str, now: datetime | None = None
    ) -> tuple[int, int, bool]:
        """Records qualifying learning activity and updates streak idempotently.
        
        Returns (current_streak, longest_streak, streak_increased_today).
        """
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        with self.db.transaction():
            # Record in activity_dates (INSERT OR IGNORE handles duplicates)
            self.db.execute(
                "INSERT OR IGNORE INTO activity_dates(learner_id, activity_date, activity_type, created_at) "
                "VALUES (?,?,?,?)",
                (learner_id, today, activity_type, now_iso)
            )

            row = self.db.one("SELECT * FROM gamification_summaries WHERE learner_id=?", (learner_id,))
            if not row:
                current_streak = 1
                longest_streak = 1
                self.db.execute(
                    "INSERT INTO gamification_summaries(learner_id, current_streak, longest_streak, last_activity_date, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (learner_id, current_streak, longest_streak, today, now_iso)
                )
                return current_streak, longest_streak, True

            current_streak = int(row["current_streak"])
            longest_streak = int(row["longest_streak"])
            last_activity_date = row["last_activity_date"]

            if last_activity_date == today:
                # Already counted today: streak does not increase multiple times on the same day
                return current_streak, longest_streak, False

            if last_activity_date == yesterday:
                # Consecutive day: increment streak
                current_streak += 1
            else:
                # Missed a calendar day: active streak resets to 1
                current_streak = 1

            longest_streak = max(longest_streak, current_streak)
            self.db.execute(
                "UPDATE gamification_summaries SET current_streak=?, longest_streak=?, last_activity_date=?, updated_at=? "
                "WHERE learner_id=?",
                (current_streak, longest_streak, today, now_iso, learner_id)
            )
            return current_streak, longest_streak, True

    def award_badge(
        self, learner_id: str, badge_id: str, reason: str, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Awards a badge if not already earned. Idempotent. Returns badge info if newly awarded."""
        if badge_id not in BADGE_CATALOG:
            return None
        now = now or datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self.db.transaction():
            cur = self.db.execute(
                "INSERT OR IGNORE INTO earned_badges(learner_id, badge_id, awarded_at, reason) "
                "VALUES (?,?,?,?)",
                (learner_id, badge_id, now_iso, reason)
            )
            if cur.rowcount > 0:
                bdef = BADGE_CATALOG[badge_id]
                res = bdef.to_dict()
                res["awarded_at"] = now_iso
                res["reason"] = reason
                return res
        return None

    def process_mastery_success(
        self, learner_id: str, session_id: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Processes a successful mastery check. Updates streak and awards badges."""
        now = now or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        newly_unlocked: list[dict[str, Any]] = []

        with self.db.transaction():
            # Update mastery count
            self.db.execute(
                "INSERT INTO gamification_summaries(learner_id, current_streak, longest_streak, mastery_passed, updated_at) "
                "VALUES (?,0,0,1,?) ON CONFLICT(learner_id) DO UPDATE SET mastery_passed=mastery_passed+1, updated_at=excluded.updated_at",
                (learner_id, now_iso)
            )

        # Update streak for qualifying activity
        cur_streak, long_streak, _ = self.record_activity_and_update_streak(learner_id, "mastery", now)

        # Award mastery badge
        b = self.award_badge(learner_id, "mastery_achieved", "Passed a fresh question mastery check", now)
        if b:
            newly_unlocked.append(b)

        # Check streak badges
        if cur_streak >= 3:
            b = self.award_badge(learner_id, "consistent_learner", "Reached a 3-day learning streak", now)
            if b:
                newly_unlocked.append(b)
        if cur_streak >= 7:
            b = self.award_badge(learner_id, "week_warrior", "Reached a 7-day learning streak", now)
            if b:
                newly_unlocked.append(b)

        return newly_unlocked

    def process_quiz_completion(
        self,
        learner_id: str,
        score: int,
        total: int,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Processes a verified quiz completion.
        
        Updates stats, daily streak, daily quests, and awards eligible badges.
        Guarantees idempotency via idempotency_key or duplicate protection.
        """
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        now_iso = now.isoformat()

        if idempotency_key:
            existing = self.db.one("SELECT id FROM quiz_completions WHERE id=?", (idempotency_key,))
            if existing:
                # Duplicate submission; return current summary without re-processing
                summary = self.get_summary(learner_id, now)
                return {
                    "already_processed": True,
                    "newly_unlocked_badges": [],
                    "summary": summary,
                }
            completion_id = idempotency_key
        else:
            completion_id = str(uuid.uuid4())

        is_perfect = (score == total and total >= 3)
        accuracy = (score / total) if total > 0 else 0.0

        with self.db.transaction():
            # Record quiz completion
            self.db.execute(
                "INSERT INTO quiz_completions(id, learner_id, quiz_type, score, total, completed_at) "
                "VALUES (?,?,'quiz',?,?,?)",
                (completion_id, learner_id, score, total, now_iso)
            )

            # Ensure summary exists and update quiz counts
            self.db.execute(
                "INSERT INTO gamification_summaries(learner_id, current_streak, longest_streak, quizzes_completed, perfect_quizzes, updated_at) "
                "VALUES (?,0,0,1,?,?) ON CONFLICT(learner_id) DO UPDATE SET "
                "quizzes_completed=quizzes_completed+1, "
                "perfect_quizzes=perfect_quizzes+?, "
                "updated_at=excluded.updated_at",
                (learner_id, int(is_perfect), now_iso, int(is_perfect))
            )

        # Update daily streak
        cur_streak, long_streak, _ = self.record_activity_and_update_streak(learner_id, "quiz", now)

        # Update Daily Quests
        newly_unlocked_badges: list[dict[str, Any]] = []
        self._update_daily_quests(learner_id, score, total, accuracy, today, now, newly_unlocked_badges)

        # Check Badge Unlocks
        summary_row = self.db.one("SELECT * FROM gamification_summaries WHERE learner_id=?", (learner_id,))
        q_count = int(summary_row["quizzes_completed"]) if summary_row else 1

        # 1. First Steps: 1+ quiz completed
        if q_count >= 1:
            b = self.award_badge(learner_id, "first_steps", "Completed first quiz", now)
            if b:
                newly_unlocked_badges.append(b)

        # 2. Perfect Score: 100% on quiz
        if is_perfect:
            b = self.award_badge(learner_id, "perfect_score", "Scored 100% on a quiz", now)
            if b:
                newly_unlocked_badges.append(b)

        # 3. Quiz Champion: 5+ quizzes completed
        if q_count >= 5:
            b = self.award_badge(learner_id, "quiz_champion", "Completed 5 quizzes", now)
            if b:
                newly_unlocked_badges.append(b)

        # 4. Consistent Learner: 3+ streak
        if cur_streak >= 3:
            b = self.award_badge(learner_id, "consistent_learner", "Reached a 3-day learning streak", now)
            if b:
                newly_unlocked_badges.append(b)

        # 5. Week Warrior: 7+ streak
        if cur_streak >= 7:
            b = self.award_badge(learner_id, "week_warrior", "Reached a 7-day learning streak", now)
            if b:
                newly_unlocked_badges.append(b)

        updated_summary = self.get_summary(learner_id, now)
        return {
            "already_processed": False,
            "newly_unlocked_badges": newly_unlocked_badges,
            "summary": updated_summary,
        }

    def _update_daily_quests(
        self,
        learner_id: str,
        score: int,
        total: int,
        accuracy: float,
        today: str,
        now: datetime,
        newly_unlocked_badges: list[dict[str, Any]],
    ) -> None:
        """Updates progress on today's quests and claims rewards if completed."""
        # Ensure quests are initialized
        self.get_daily_quests(learner_id, now)
        now_iso = now.isoformat()

        rows = self.db.all("SELECT * FROM daily_quests WHERE learner_id=? AND date=?", (learner_id, today))
        for row in rows:
            qid = row["quest_id"]
            progress = int(row["progress"])
            target = int(row["target"])
            completed = bool(row["completed"])
            reward_claimed = bool(row["reward_claimed"])
            reward_badge_id = row["reward_badge_id"]

            new_progress = progress
            if qid == "quest_fraction_explorer":
                new_progress = progress + 1
            elif qid == "quest_accuracy":
                if accuracy >= 0.8:
                    new_progress = max(progress, 1)
            elif qid == "quest_daily_practice":
                new_progress = progress + 1

            is_now_complete = (new_progress >= target)
            with self.db.transaction():
                self.db.execute(
                    "UPDATE daily_quests SET progress=?, completed=?, completed_at=COALESCE(completed_at, ?) "
                    "WHERE id=?",
                    (new_progress, int(is_now_complete), now_iso if is_now_complete else None, row["id"])
                )

                if is_now_complete and not completed:
                    self.db.execute(
                        "UPDATE gamification_summaries SET quests_completed=quests_completed+1 WHERE learner_id=?",
                        (learner_id,)
                    )

                if is_now_complete and not reward_claimed:
                    self.db.execute(
                        "UPDATE daily_quests SET reward_claimed=1 WHERE id=?",
                        (row["id"],)
                    )
                    b = self.award_badge(learner_id, reward_badge_id, f"Completed daily quest: {qid}", now)
                    if b:
                        newly_unlocked_badges.append(b)

                    # Also award Daily Quest Hero if not already awarded
                    b_hero = self.award_badge(learner_id, "daily_quest_hero", "Completed a daily learning quest", now)
                    if b_hero:
                        newly_unlocked_badges.append(b_hero)
