"""Small SQLite wrapper (thread-safe, explicit transactions)."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    submitted TEXT NOT NULL,
    correct INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- practice | mastery
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_learner ON attempts(learner_id, id);
CREATE TABLE IF NOT EXISTS diagnostic_sessions (
    id TEXT PRIMARY KEY,
    attempt_id INTEGER NOT NULL,
    learner_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    scores TEXT NOT NULL,            -- JSON {pattern: points}
    evidence TEXT NOT NULL,          -- JSON [str]
    asked TEXT NOT NULL,             -- JSON [{followup_id, option_id}]
    status TEXT NOT NULL,            -- awaiting_followup | resolved
    selected_pattern TEXT,
    outcome TEXT,                    -- targeted | general
    confidence_share REAL,
    mastery_questions TEXT NOT NULL, -- JSON [question ids issued]
    mastery_result TEXT,             -- passed | failed
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_learner ON diagnostic_sessions(learner_id);
CREATE TABLE IF NOT EXISTS mastery_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    learner_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    submitted TEXT NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    due_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_review_learner ON review_queue(learner_id, done);
CREATE TABLE IF NOT EXISTS gamification_summaries (
    learner_id TEXT PRIMARY KEY,
    current_streak INTEGER NOT NULL DEFAULT 0,
    longest_streak INTEGER NOT NULL DEFAULT 0,
    last_activity_date TEXT,
    quizzes_completed INTEGER NOT NULL DEFAULT 0,
    perfect_quizzes INTEGER NOT NULL DEFAULT 0,
    quests_completed INTEGER NOT NULL DEFAULT 0,
    mastery_passed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    activity_date TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(learner_id, activity_date, activity_type)
);
CREATE INDEX IF NOT EXISTS idx_activity_dates_learner ON activity_dates(learner_id, activity_date);
CREATE TABLE IF NOT EXISTS earned_badges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    badge_id TEXT NOT NULL,
    awarded_at TEXT NOT NULL,
    reason TEXT,
    UNIQUE(learner_id, badge_id)
);
CREATE INDEX IF NOT EXISTS idx_badges_learner ON earned_badges(learner_id);
CREATE TABLE IF NOT EXISTS quiz_completions (
    id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    quiz_type TEXT NOT NULL,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_completions_learner ON quiz_completions(learner_id);
CREATE TABLE IF NOT EXISTS daily_quests (
    id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    quest_id TEXT NOT NULL,
    date TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    target INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    reward_claimed INTEGER NOT NULL DEFAULT 0,
    reward_badge_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quests_learner_date ON daily_quests(learner_id, date);
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            folder = os.path.dirname(os.path.abspath(path))
            os.makedirs(folder, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._tx_depth = 0
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self):
        with self._lock:
            nested = self._tx_depth > 0
            if not nested:
                self._conn.execute("BEGIN")
            self._tx_depth += 1
            try:
                yield
            except BaseException:
                if not nested:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                if not nested:
                    self._conn.execute("COMMIT")
            finally:
                self._tx_depth -= 1

    def close(self) -> None:
        with self._lock:
            self._conn.close()
