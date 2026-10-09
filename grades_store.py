"""SQLite store for logged course grades (used by the GPA tools)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import config

DB_PATH = config.data_path("grades.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                term       TEXT,
                course     TEXT NOT NULL,
                credits    REAL NOT NULL,
                grade      TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def list_grades() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM grades ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def add_grade(course: str, credits: float, grade: str, term: str | None = None) -> int:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO grades (term, course, credits, grade, created_at) VALUES (?, ?, ?, ?, ?)",
            (term, course, credits, grade, ts),
        )
        return int(cur.lastrowid)


def delete_grade(grade_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM grades WHERE id = ?", (grade_id,))
