"""SQLite store for assignments/exams, with cross-source de-duplication.

Items can come from a syllabus (Claude-extracted) or from Canvas. The dedup logic
ensures the same assignment isn't added twice: an incoming item that matches an
existing one (same course, similar title, close due date — or an exact Canvas id)
updates/merges the existing row instead of inserting a new one.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher

import config

DB_PATH = config.data_path("assignments.db")

# Similarity thresholds for the cross-check.
_RATIO_WITH_DATE = 0.6      # titles this similar + due dates within a day = same item
_RATIO_NO_DATE = 0.85       # stricter when a due date is missing
_DUE_WINDOW_DAYS = 1

_CRSE = re.compile(r"([A-Za-z]{2,4})\s*0*(\d{2,3}[A-Za-z]?)")
_SOURCE_ORDER = {"syllabus": 0, "canvas": 1, "manual": 2}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                course          TEXT,
                title           TEXT NOT NULL,
                type            TEXT DEFAULT 'assignment',
                due_at          TEXT,
                weight          REAL,
                source          TEXT DEFAULT 'manual',
                canvas_id       TEXT,
                canvas_submitted INTEGER DEFAULT 0,
                canvas_submission_status TEXT,
                canvas_submitted_at TEXT,
                canvas_missing   INTEGER DEFAULT 0,
                done            INTEGER DEFAULT 0,
                google_event_id TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(assignments)").fetchall()
        }
        migrations = {
            "canvas_submitted": "ALTER TABLE assignments ADD COLUMN canvas_submitted INTEGER DEFAULT 0",
            "canvas_submission_status": "ALTER TABLE assignments ADD COLUMN canvas_submission_status TEXT",
            "canvas_submitted_at": "ALTER TABLE assignments ADD COLUMN canvas_submitted_at TEXT",
            "canvas_missing": "ALTER TABLE assignments ADD COLUMN canvas_missing INTEGER DEFAULT 0",
        }
        for col, sql in migrations.items():
            if col not in existing:
                conn.execute(sql)


# ── Normalization ────────────────────────────────────────────────────────────
def _course_key(c: str | None) -> str:
    m = _CRSE.search(c or "")
    if m:
        return f"{m.group(1).upper()} {m.group(2).upper()}"
    return re.sub(r"\s+", " ", (c or "").strip().lower())


def _norm_title(t: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (t or "").lower())).strip()


def _due_date(due_at: str | None):
    if not due_at:
        return None
    try:
        d = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(config.LOCAL_TZ)
        return d.date()
    except ValueError:
        try:
            return datetime.strptime(due_at[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


# ── Queries ──────────────────────────────────────────────────────────────────
def list_assignments() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM assignments ORDER BY (due_at IS NULL), due_at, course"
        ).fetchall()
    return [dict(r) for r in rows]


def get(assignment_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    return dict(row) if row else None


_KEYWORD_GROUPS = [
    {"hw", "homework", "assignment"},
    {"midterm", "final", "exam"},
    {"quiz"},
    {"project"},
    {"paper", "essay"},
    {"lab"},
    {"discussion", "response"},
]


def _keyword_group(norm_title: str) -> set[str] | None:
    words = set(norm_title.split())
    for group in _KEYWORD_GROUPS:
        if words & group:
            return group
    return None


def _same_item_despite_date_gap(existing_title: str, incoming_title: str) -> bool:
    """Looser same-item check used only against a Canvas-confirmed row.

    A regex-parsed syllabus guess can have a due date that's wildly wrong (off
    by days or weeks), so date proximity can't be trusted the way the normal
    check requires. Plain fuzzy string ratio doesn't work either here — e.g.
    "Homework 3" vs "Hw 3" (same item) scores lower than "Homework 3" vs
    "Homework 4" (different items) — so match on assignment kind + number
    instead, or a title prefix for descriptive suffixes like
    "Midterm Exam" vs "Midterm Exam (Material Covered Up To Day 10)".
    """
    ne, ni = _norm_title(existing_title), _norm_title(incoming_title)
    if ne == ni:
        return True
    group = _keyword_group(ne)
    if not group or group != _keyword_group(ni):
        return False
    nums_e, nums_i = re.findall(r"\d+", ne), re.findall(r"\d+", ni)
    if nums_e and nums_i:
        return nums_e == nums_i
    return ne.startswith(ni) or ni.startswith(ne)


def find_duplicate(course: str, title: str, due_at: str | None) -> int | None:
    """Return the id of an existing assignment that is the same item, or None."""
    ck, nt, d = _course_key(course), _norm_title(title), _due_date(due_at)
    for a in list_assignments():
        if _course_key(a["course"]) != ck:
            continue
        ratio = SequenceMatcher(None, nt, _norm_title(a["title"])).ratio()
        ad = _due_date(a["due_at"])
        if d and ad:
            if abs((d - ad).days) <= _DUE_WINDOW_DAYS and ratio >= _RATIO_WITH_DATE:
                return a["id"]
        elif ratio >= _RATIO_NO_DATE:
            return a["id"]
        if a["canvas_id"] and _same_item_despite_date_gap(a["title"], title):
            return a["id"]
    return None


def _find_by_canvas(canvas_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM assignments WHERE canvas_id = ?", (canvas_id,)
        ).fetchone()
    return dict(row) if row else None


def _merge_sources(existing: str, incoming: str) -> str:
    parts = {p for p in (existing or "").split("+") if p} | {incoming}
    return "+".join(sorted(parts, key=lambda s: _SOURCE_ORDER.get(s, 9)))


def _update(assignment_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE assignments SET {cols} WHERE id = ?", (*fields.values(), assignment_id)
        )


def add_assignment(
    course: str,
    title: str,
    type: str = "assignment",
    due_at: str | None = None,
    weight: float | None = None,
    source: str = "manual",
    canvas_id: str | None = None,
    canvas_submitted: bool = False,
    canvas_submission_status: str | None = None,
    canvas_submitted_at: str | None = None,
    canvas_missing: bool = False,
) -> tuple[int, bool]:
    """Insert, or merge into an existing match. Returns (id, was_new)."""
    canvas_done = 1 if canvas_submitted else None

    # 1. Exact Canvas re-import → update in place.
    if canvas_id and (row := _find_by_canvas(canvas_id)):
        fields = {
            "due_at": due_at or row["due_at"],
            "weight": weight if weight is not None else row["weight"],
            "title": title or row["title"],
            "canvas_submitted": 1 if canvas_submitted else 0,
            "canvas_submission_status": canvas_submission_status,
            "canvas_submitted_at": canvas_submitted_at,
            "canvas_missing": 1 if canvas_missing else 0,
        }
        if canvas_done is not None:
            fields["done"] = canvas_done
        _update(row["id"], **fields)
        return row["id"], False

    # 2. Fuzzy cross-source duplicate → merge.
    dup_id = find_duplicate(course, title, due_at)
    if dup_id:
        row = get(dup_id)
        # Canvas is the source of truth for due dates — it always wins over a
        # syllabus-extracted guess, even when that guess landed first.
        merged_due_at = due_at if (source == "canvas" and due_at) else (row["due_at"] or due_at)
        fields = {
            "due_at": merged_due_at,
            "weight": row["weight"] if row["weight"] is not None else weight,
            "canvas_id": row["canvas_id"] or canvas_id,
            "source": _merge_sources(row["source"], source),
            "type": row["type"] or type,
        }
        if canvas_id:
            fields.update(
                {
                    "canvas_submitted": 1 if canvas_submitted else 0,
                    "canvas_submission_status": canvas_submission_status,
                    "canvas_submitted_at": canvas_submitted_at,
                    "canvas_missing": 1 if canvas_missing else 0,
                }
            )
            if canvas_done is not None:
                fields["done"] = canvas_done
        _update(dup_id, **fields)
        return dup_id, False

    # 3. New row.
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO assignments (
                course, title, type, due_at, weight, source, canvas_id,
                canvas_submitted, canvas_submission_status, canvas_submitted_at,
                canvas_missing, done, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course,
                title,
                type,
                due_at,
                weight,
                source,
                canvas_id,
                1 if canvas_submitted else 0,
                canvas_submission_status,
                canvas_submitted_at,
                1 if canvas_missing else 0,
                1 if canvas_submitted else 0,
                ts,
            ),
        )
        return int(cur.lastrowid), True


def set_done(assignment_id: int, done: bool) -> None:
    _update(assignment_id, done=1 if done else 0)


def set_event_id(assignment_id: int, event_id: str | None) -> None:
    _update(assignment_id, google_event_id=event_id)


def delete(assignment_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
