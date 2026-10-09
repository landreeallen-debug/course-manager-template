"""SQLite store for the two living study documents: class notes and cheat sheet.

processed_files tracks every Canvas file that has been looked at so we don't
re-process unchanged files. course_notes and cheat_sheet are one document per
course, updated cumulatively as new slides/homework arrive.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

DB_PATH = config.data_path("study_notes.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                canvas_file_id  TEXT NOT NULL PRIMARY KEY,
                course          TEXT NOT NULL,
                file_name       TEXT NOT NULL,
                classification  TEXT NOT NULL,
                file_updated_at TEXT,
                processed_at    TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_notes (
                course     TEXT NOT NULL PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cheat_sheet (
                course     TEXT NOT NULL PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_knowledge (
                course     TEXT NOT NULL PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_materials (
                canvas_file_id  TEXT NOT NULL PRIMARY KEY,
                course          TEXT NOT NULL,
                file_name       TEXT NOT NULL,
                content         TEXT NOT NULL DEFAULT '',
                file_updated_at TEXT,
                extracted_at    TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_status (
                course        TEXT NOT NULL PRIMARY KEY,
                status        TEXT NOT NULL,
                message       TEXT NOT NULL DEFAULT '',
                files_seen    INTEGER NOT NULL DEFAULT 0,
                files_added   INTEGER NOT NULL DEFAULT 0,
                files_skipped INTEGER NOT NULL DEFAULT 0,
                errors_json   TEXT NOT NULL DEFAULT '[]',
                synced_at     TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tutor_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course      TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tutor_messages_course ON tutor_messages(course, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tutor_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course      TEXT NOT NULL,
                title       TEXT NOT NULL,
                file_name   TEXT NOT NULL,
                mime_type   TEXT NOT NULL DEFAULT 'text/markdown',
                content     TEXT NOT NULL DEFAULT '',
                prompt      TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS textbook_pages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                course      TEXT NOT NULL,
                label       TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                uploaded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS graded_work (
                canvas_id    TEXT NOT NULL PRIMARY KEY,
                course       TEXT NOT NULL,
                title        TEXT NOT NULL,
                score        REAL,
                processed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mistake_notes (
                course     TEXT NOT NULL PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )


# ── Processed files ───────────────────────────────────────────────────────────

def get_processed(canvas_file_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM processed_files WHERE canvas_file_id = ?", (canvas_file_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_processed(
    canvas_file_id: str,
    course: str,
    file_name: str,
    classification: str,
    file_updated_at: str | None = None,
) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO processed_files
                (canvas_file_id, course, file_name, classification, file_updated_at, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(canvas_file_id) DO UPDATE SET
                classification  = excluded.classification,
                file_updated_at = excluded.file_updated_at,
                processed_at    = excluded.processed_at
            """,
            (canvas_file_id, course, file_name, classification, file_updated_at, ts),
        )


def clear_processed_for_course(course: str) -> None:
    """Remove all processed_files records for a course so they can be re-reviewed."""
    with _connect() as conn:
        conn.execute("DELETE FROM processed_files WHERE course = ?", (course,))


def clear_processed_file(canvas_file_id: str) -> None:
    """Remove one processed_files record so the file can be reviewed again."""
    with _connect() as conn:
        conn.execute("DELETE FROM processed_files WHERE canvas_file_id = ?", (canvas_file_id,))


def clear_notes_for_course(course: str) -> None:
    """Delete the stored notes and cheat sheet for a course."""
    with _connect() as conn:
        conn.execute("DELETE FROM course_notes WHERE course = ?", (course,))
        conn.execute("DELETE FROM cheat_sheet WHERE course = ?", (course,))


def list_processed(course: str | None = None) -> list[dict]:
    with _connect() as conn:
        if course:
            rows = conn.execute(
                "SELECT * FROM processed_files WHERE course = ? ORDER BY processed_at",
                (course,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM processed_files ORDER BY course, processed_at"
            ).fetchall()
    return [dict(r) for r in rows]


# ── Class notes ───────────────────────────────────────────────────────────────

def get_course_notes(course: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM course_notes WHERE course = ?", (course,)
        ).fetchone()
    return dict(row) if row else None


def upsert_course_notes(course: str, content: str) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO course_notes (course, content, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(course) DO UPDATE SET
                content    = excluded.content,
                updated_at = excluded.updated_at
            """,
            (course, content, ts),
        )


def list_course_notes() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM course_notes ORDER BY course").fetchall()
    return [dict(r) for r in rows]


# ── Cheat sheet ───────────────────────────────────────────────────────────────

def get_cheat_sheet(course: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM cheat_sheet WHERE course = ?", (course,)
        ).fetchone()
    return dict(row) if row else None


def upsert_cheat_sheet(course: str, content: str) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cheat_sheet (course, content, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(course) DO UPDATE SET
                content    = excluded.content,
                updated_at = excluded.updated_at
            """,
            (course, content, ts),
        )


def list_cheat_sheets() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM cheat_sheet ORDER BY course").fetchall()
    return [dict(r) for r in rows]


# ── Textbook pages ────────────────────────────────────────────────────────────

def add_textbook_page(course: str, label: str, file_path: str) -> int:
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO textbook_pages (course, label, file_path, uploaded_at) VALUES (?,?,?,?)",
            (course, label, file_path, ts),
        )
        return int(cur.lastrowid)


def list_textbook_pages(course: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM textbook_pages WHERE course=? ORDER BY uploaded_at",
            (course,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Course knowledge ─────────────────────────────────────────────────────────

def get_course_knowledge(course: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM course_knowledge WHERE course=?", (course,)).fetchone()
    return dict(row) if row else None


def upsert_course_knowledge(course: str, content: str) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO course_knowledge (course, content, updated_at) VALUES (?,?,?)
            ON CONFLICT(course) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
            """,
            (course, content, ts),
        )


# ── Course materials ─────────────────────────────────────────────────────────

def get_course_material(canvas_file_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM course_materials WHERE canvas_file_id=?",
            (canvas_file_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_course_material(
    canvas_file_id: str,
    course: str,
    file_name: str,
    content: str,
    file_updated_at: str | None = None,
) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO course_materials
                (canvas_file_id, course, file_name, content, file_updated_at, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(canvas_file_id) DO UPDATE SET
                course          = excluded.course,
                file_name       = excluded.file_name,
                content         = excluded.content,
                file_updated_at = excluded.file_updated_at,
                extracted_at    = excluded.extracted_at
            """,
            (canvas_file_id, course, file_name, content, file_updated_at, ts),
        )


def list_course_materials(course: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM course_materials WHERE course=? ORDER BY file_name",
            (course,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Sync status ──────────────────────────────────────────────────────────────

def upsert_sync_status(
    course: str,
    status: str,
    message: str = "",
    files_seen: int = 0,
    files_added: int = 0,
    files_skipped: int = 0,
    errors: list[str] | None = None,
) -> None:
    import json

    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_status
                (course, status, message, files_seen, files_added, files_skipped, errors_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(course) DO UPDATE SET
                status = excluded.status,
                message = excluded.message,
                files_seen = excluded.files_seen,
                files_added = excluded.files_added,
                files_skipped = excluded.files_skipped,
                errors_json = excluded.errors_json,
                synced_at = excluded.synced_at
            """,
            (
                course,
                status,
                message,
                files_seen,
                files_added,
                files_skipped,
                json.dumps(errors or []),
                ts,
            ),
        )


def get_sync_status(course: str) -> dict | None:
    import json

    with _connect() as conn:
        row = conn.execute("SELECT * FROM sync_status WHERE course=?", (course,)).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["errors"] = json.loads(data.pop("errors_json") or "[]")
    except Exception:
        data["errors"] = []
    return data


# ── Tutor conversation log (auto-saved — no manual save step) ───────────────

def log_tutor_message(course: str, session_id: str, role: str, content: str) -> int:
    """Persist one chat turn immediately so nothing is ever lost unsaved."""
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO tutor_messages (course, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (course, session_id, role, content, ts),
        )
        return int(cur.lastrowid)


def list_tutor_sessions(course: str, limit: int = 30) -> list[dict]:
    """One row per past conversation for this course, most recently active first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id,
                   MIN(created_at) AS started_at,
                   MAX(created_at) AS last_at,
                   COUNT(*)        AS message_count
            FROM tutor_messages
            WHERE course = ?
            GROUP BY session_id
            ORDER BY last_at DESC
            LIMIT ?
            """,
            (course, limit),
        ).fetchall()
        sessions = []
        for row in rows:
            first_user = conn.execute(
                """
                SELECT content FROM tutor_messages
                WHERE course = ? AND session_id = ? AND role = 'user'
                ORDER BY id LIMIT 1
                """,
                (course, row["session_id"]),
            ).fetchone()
            sessions.append(
                {
                    "session_id": row["session_id"],
                    "started_at": row["started_at"],
                    "last_at": row["last_at"],
                    "message_count": row["message_count"],
                    "preview": (first_user["content"][:120] if first_user else ""),
                }
            )
    return sessions


def load_tutor_session(course: str, session_id: str) -> list[dict]:
    """Full transcript of one past conversation, in order."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content, created_at FROM tutor_messages
            WHERE course = ? AND session_id = ?
            ORDER BY id
            """,
            (course, session_id),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_tutor_context(
    course: str,
    exclude_session_id: str | None = None,
    limit_sessions: int = 8,
    snippet_chars: int = 300,
) -> str:
    """Compact summary of past conversations for the tutor's system prompt."""
    sessions = list_tutor_sessions(course, limit=limit_sessions + 1)
    parts: list[str] = []
    for s in sessions:
        if exclude_session_id and s["session_id"] == exclude_session_id:
            continue
        if len(parts) >= limit_sessions:
            break
        msgs = load_tutor_session(course, s["session_id"])
        if not msgs:
            continue
        lines = [f"--- Session {s['started_at'][:10]} ---"]
        for m in msgs:
            snippet = m["content"][:snippet_chars].replace("\n", " ")
            if len(m["content"]) > snippet_chars:
                snippet += "..."
            lines.append(f"{m['role'].capitalize()}: {snippet}")
        parts.append("\n".join(lines))
    if not parts:
        return ""
    return "PAST CONVERSATIONS — use these to recall what was discussed in earlier sessions:\n\n" + "\n\n".join(parts)


def search_tutor_messages(course: str, query: str, limit: int = 20) -> list[dict]:
    """Substring search across every saved chat message for this course."""
    query = (query or "").strip()
    if not query:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id, role, content, created_at FROM tutor_messages
            WHERE course = ? AND content LIKE ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (course, f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Tutor-generated files ───────────────────────────────────────────────────

def save_tutor_file(
    course: str,
    title: str,
    file_name: str,
    content: str,
    prompt: str = "",
    mime_type: str = "text/markdown",
) -> int:
    """Persist a tutor-generated downloadable file."""
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tutor_files
                (course, title, file_name, mime_type, content, prompt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (course, title, file_name, mime_type, content, prompt, ts),
        )
        return int(cur.lastrowid)


def list_tutor_files(course: str, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM tutor_files
            WHERE course=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (course, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_tutor_file(file_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tutor_files WHERE id=?", (file_id,)).fetchone()
    return dict(row) if row else None


def delete_tutor_file(file_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM tutor_files WHERE id=?", (file_id,))


# ── Graded work → recurring-mistake notes ────────────────────────────────────

def get_graded_work(canvas_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM graded_work WHERE canvas_id = ?", (canvas_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_graded_processed(canvas_id: str, course: str, title: str, score: float | None) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO graded_work (canvas_id, course, title, score, processed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(canvas_id) DO UPDATE SET
                title        = excluded.title,
                score        = excluded.score,
                processed_at = excluded.processed_at
            """,
            (canvas_id, course, title, score, ts),
        )


def get_mistake_notes(course: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mistake_notes WHERE course = ?", (course,)
        ).fetchone()
    return dict(row) if row else None


def upsert_mistake_notes(course: str, content: str) -> None:
    ts = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO mistake_notes (course, content, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(course) DO UPDATE SET
                content    = excluded.content,
                updated_at = excluded.updated_at
            """,
            (course, content, ts),
        )


def delete_textbook_page(page_id: int) -> str | None:
    """Delete the DB record and return the file_path so the caller can delete the file."""
    with _connect() as conn:
        row = conn.execute("SELECT file_path FROM textbook_pages WHERE id=?", (page_id,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM textbook_pages WHERE id=?", (page_id,))
    return row["file_path"]
