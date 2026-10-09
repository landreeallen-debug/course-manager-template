"""Home-screen sync and upcoming-work extraction."""
from __future__ import annotations

import datetime as dt
import re

import assignments_store
import canvas
import config
import study_notes_store as store

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_MONTH_RE = re.compile(
    r"\b("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")\.?\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
    re.I,
)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_WORK_RE = re.compile(
    r"\b("
    r"homework|hw|assignment|problem\s+set|worksheet|quiz|exam|midterm|final|"
    r"project|paper|essay|presentation|lab|reading|response|discussion|draft"
    r")\b",
    re.I,
)
_NOISE_RE = re.compile(
    r"\b(office\s+hours?|room|location|email|phone|zoom|webex|canvas\s+site)\b",
    re.I,
)
_SYLLABUS_FILE_RE = re.compile(r"(syllabus|schedule|calendar|course[_\s-]*(?:info|information))", re.I)
_MAX_SYLLABUS_BYTES = 12 * 1024 * 1024


def greeting(now: dt.datetime | None = None, name: str | None = None) -> str:
    """Return a time-of-day greeting."""
    hour = (now or dt.datetime.now()).hour
    if hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    return f"{part}, {name or config.env_text('STUDENT_NAME') or 'there'}."


def _date_from_match(match: re.Match, today: dt.date) -> dt.date | None:
    month_key = match.group(1).lower().rstrip(".")
    month = _MONTHS.get(month_key)
    if month is None:
        return None
    day = int(match.group(2))
    year = int(match.group(3) or today.year)
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _numeric_date_from_match(match: re.Match, today: dt.date) -> dt.date | None:
    month = int(match.group(1))
    day = int(match.group(2))
    year_text = match.group(3)
    year = today.year
    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _line_date(line: str, today: dt.date) -> tuple[dt.date | None, re.Match | None]:
    month_match = _MONTH_RE.search(line)
    if month_match:
        return _date_from_match(month_match, today), month_match
    numeric_match = _NUMERIC_DATE_RE.search(line)
    if numeric_match:
        return _numeric_date_from_match(numeric_match, today), numeric_match
    return None, None


def _clean_title(line: str, date_match: re.Match | None) -> str:
    text = re.sub(r"\s+", " ", line).strip(" -\t")
    text = re.sub(r"^[*-]\s*", "", text)
    if date_match:
        text = text.replace(date_match.group(0), " ", 1).strip(" -:")
    text = re.sub(r"^week\s*\d{1,2}\s*[:.-]?\s*", "", text, flags=re.I)
    text = re.sub(r"^\d{1,2}\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:m|mon|t|tu|tue|w|wed|th|thu|f|fri|sa|sat|su|sun)\s+", "", text, flags=re.I)
    text = re.sub(r"\b(?:due|assigned|by|on)\b", " ", text, flags=re.I)
    text = re.sub(r"\(\s*\d{4}\s*-\s*\)", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:")
    text = re.sub(r"^(?:m|mon|t|tu|tue|w|wed|th|thu|f|fri|sa|sat|su|sun)\s+", "", text, flags=re.I)

    if not text:
        return "Syllabus item"
    return text[:120]


def _item_type(title: str) -> str:
    lower = title.lower()
    if any(word in lower for word in ("exam", "midterm", "final", "quiz")):
        return "exam"
    if any(word in lower for word in ("reading", "discussion")):
        return "reading"
    return "assignment"


def extract_syllabus_items(
    course: str,
    text: str,
    today: dt.date | None = None,
    horizon_days: int = 210,
) -> list[dict]:
    """Extract dated assignments/exams from syllabus-like text."""
    today = today or dt.date.today()
    cutoff_start = today - dt.timedelta(days=21)
    cutoff_end = today + dt.timedelta(days=horizon_days)
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) > 260:
            continue
        if not _WORK_RE.search(line) or _NOISE_RE.search(line):
            continue

        due_date, date_match = _line_date(line, today)
        if not due_date or due_date < cutoff_start or due_date > cutoff_end:
            continue

        title = _clean_title(line, date_match).title()
        if title.lower() in {"syllabus item", "assignment"}:
            continue
        key = (title.lower(), due_date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "course": course,
                "title": title,
                "type": _item_type(title),
                "due_at": due_date.isoformat(),
                "source": "syllabus",
            }
        )
    return items


def import_syllabus_items(course: str, text: str, source_label: str = "") -> int:
    """Add syllabus-derived items to the assignment store."""
    added = 0
    for item in extract_syllabus_items(course, text):
        title = item["title"]
        if source_label:
            title = f"{title}"
        _, was_new = assignments_store.add_assignment(
            course=item["course"],
            title=title,
            type=item["type"],
            due_at=item["due_at"],
            source=item["source"],
        )
        if was_new:
            added += 1
    return added


def import_stored_material_items(course: str) -> int:
    """Import dated work from already-indexed syllabus/schedule materials."""
    added = 0
    for material in store.list_course_materials(course):
        if not _is_syllabus_like_file(material.get("file_name", "")):
            continue
        added += import_syllabus_items(
            course,
            material.get("content") or "",
            material.get("file_name", ""),
        )
    return added


def _is_syllabus_like_file(name: str) -> bool:
    return bool(_SYLLABUS_FILE_RE.search(name or ""))


def _material_text(file: dict) -> str:
    existing = store.get_course_material(str(file.get("canvas_file_id") or ""))
    if existing and existing.get("file_updated_at") == file.get("updated_at"):
        return existing.get("content") or ""
    if (file.get("size") or 0) > _MAX_SYLLABUS_BYTES:
        return existing.get("content") if existing else ""

    from notes_engine import extract_text

    file_bytes = canvas.download_file_by_id(str(file["canvas_file_id"]))
    text = extract_text(file_bytes, file.get("name", ""))
    if text.strip():
        store.upsert_course_material(
            str(file["canvas_file_id"]),
            file.get("course", ""),
            file.get("name", ""),
            text,
            file.get("updated_at"),
        )
    return text


def sync_from_canvas() -> dict:
    """Lightweight Home-page sync: Canvas courses and assignment due dates only.

    Syllabus/file scraping (course knowledge, linked files, exam dates buried in a
    syllabus PDF) is comparatively slow — dozens of Canvas API calls and file
    downloads per course — so it's reserved for the Classes page, which already
    does that full sync to ground the Tutor. Home just needs a fast to-do list.
    """
    assignments_store.init_db()
    store.init_db()

    if not canvas.is_configured():
        return {"status": "not_configured", "message": "Canvas is not connected."}

    result = {
        "status": "ok",
        "courses": 0,
        "canvas_added": 0,
        "syllabus_added": 0,
        "errors": [],
    }

    try:
        courses = canvas.active_courses()
    except Exception as exc:
        return {"status": "error", "message": f"Could not load Canvas courses: {exc}", "errors": [str(exc)]}

    result["courses"] = len(courses)

    try:
        for assignment in canvas.assignments():
            _, was_new = assignments_store.add_assignment(
                course=assignment["course"],
                title=assignment["title"],
                type="assignment",
                due_at=assignment.get("due_at"),
                source="canvas",
                canvas_id=assignment.get("canvas_id"),
                canvas_submitted=bool(assignment.get("canvas_submitted")),
                canvas_submission_status=assignment.get("canvas_submission_status"),
                canvas_submitted_at=assignment.get("canvas_submitted_at"),
                canvas_missing=bool(assignment.get("canvas_missing")),
            )
            if was_new:
                result["canvas_added"] += 1
    except Exception as exc:
        result["errors"].append(f"Canvas assignments: {exc}")

    # Pick up any exam/syllabus dates already extracted from course knowledge and
    # files the Classes page has synced this session — no new Canvas calls here.
    for course in courses:
        code = course["code"]
        try:
            result["syllabus_added"] += import_stored_material_items(code)
        except Exception as exc:
            result["errors"].append(f"{code} stored syllabus materials: {exc}")

    if result["errors"]:
        result["status"] = "partial"
    return result
