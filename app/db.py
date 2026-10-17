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
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            folder = os.path.dirname(os.path.abspath(path))
            os.makedirs(folder, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
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
            self._conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
