"""Minimal Canvas client using a personal access token.

The student generates a token in Canvas (Account → Settings → "+ New Access
Token"). It and the base URL are stored in a gitignored `canvas_config.json`.
Used to import assignments, which are then de-duplicated against syllabus-extracted
ones by `assignments_store`.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

import config

CONFIG_PATH = config.data_path("canvas_config.json")
DEFAULT_BASE_URL = "https://uncch.instructure.com"

_CRSE = re.compile(r"([A-Za-z]{2,4})\s*0*(\d{2,3}[A-Za-z]?)")


def is_configured() -> bool:
    if config.env_text("CANVAS_TOKEN"):
        return True
    try:
        c = json.loads(CONFIG_PATH.read_text())
        return bool(c.get("base_url") and c.get("token"))
    except Exception:
        return False


def load_config() -> dict:
    env_token = config.env_text("CANVAS_TOKEN")
    env_base = config.env_text("CANVAS_BASE_URL")
    if env_token:
        return {
            "base_url": (env_base or DEFAULT_BASE_URL).rstrip("/"),
            "token": env_token,
        }
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {"base_url": DEFAULT_BASE_URL, "token": ""}


def save_config(base_url: str, token: str) -> None:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"base_url": base, "token": token.strip()}))


def _get_with_retry(client, url: str, **kwargs):
    """GET with a couple of retries for transient connection drops.

    Canvas (or the network path to it) occasionally closes a connection
    mid-request with no response — a retry almost always succeeds.
    """
    import time

    import httpx

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return client.get(url, **kwargs)
        except httpx.TransportError as e:
            last_exc = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _get_all(path: str, params: dict | None = None) -> list:
    """GET a Canvas collection, following Link rel="next" pagination."""
    import httpx

    cfg = load_config()
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    next_url: str | None = cfg["base_url"].rstrip("/") + path
    params = {**(params or {}), "per_page": 100}
    out, first, pages = [], True, 0

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        while next_url and pages < 50:
            resp = _get_with_retry(client, next_url, headers=headers, params=params if first else None)
            resp.raise_for_status()
            data = resp.json()
            out.extend(data if isinstance(data, list) else [data])
            next_url = None
            for part in (resp.headers.get("link") or "").split(","):
                seg = part.split(";")
                if len(seg) >= 2 and 'rel="next"' in seg[1]:
                    next_url = seg[0].strip().strip("<>")
            first, pages = False, pages + 1
    return out


def _course_code(course: dict) -> str:
    raw = course.get("course_code") or course.get("name") or ""
    m = _CRSE.search(raw)
    return f"{m.group(1).upper()} {m.group(2).upper()}" if m else raw


def active_courses() -> list[dict]:
    courses = _get_all("/api/v1/courses", {"enrollment_state": "active"})
    result = [
        {"id": c["id"], "code": _course_code(c), "name": c.get("name", "")}
        for c in courses
        if isinstance(c, dict) and c.get("id")
    ]
    # Two distinct Canvas courses (e.g. a "Math Plus" support section cross-listed
    # under the same department + number as the real lecture) can collapse to the
    # same regex-parsed code. Everything downstream — tabs, assignments, tutor
    # knowledge, materials — is keyed by this code string, so a collision silently
    # merges two unrelated courses together. Disambiguate with the course name.
    counts = Counter(r["code"] for r in result)
    for r in result:
        if counts[r["code"]] > 1:
            r["code"] = f"{r['code']} ({r['name']})"
    return result


def current_grades() -> dict[int, dict]:
    """Current overall score/letter grade per active course, keyed by course id.

    Keyed by id rather than code since two distinct Canvas courses can share
    a display code. One cheap call (course list + enrollment totals) — safe
    to fetch up front for a home-screen grid, unlike the per-course
    assignment/file/syllabus sync.
    """
    courses = _get_all(
        "/api/v1/courses", {"enrollment_state": "active", "include[]": "total_scores"}
    )
    out = {}
    for c in courses:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        enrollments = c.get("enrollments") or []
        enrollment = enrollments[0] if enrollments and isinstance(enrollments[0], dict) else {}
        out[c["id"]] = {
            "score": enrollment.get("computed_current_score"),
            "grade": enrollment.get("computed_current_grade"),
        }
    return out


def assignments() -> list[dict]:
    """All assignments across active courses, normalized for the store."""
    out = []
    for c in active_courses():
        try:
            items = _get_all(
                f"/api/v1/courses/{c['id']}/assignments",
                {"include[]": "submission"},
            )
        except Exception:
            continue  # skip a course we can't read rather than failing the import
        for a in items:
            if not isinstance(a, dict) or not a.get("name"):
                continue
            submitted, status, submitted_at, missing = _submission_state(a)
            sub = a.get("submission") if isinstance(a.get("submission"), dict) else {}
            out.append(
                {
                    "course": c["code"],
                    "course_id": c["id"],
                    "title": a.get("name", ""),
                    "due_at": a.get("due_at"),  # ISO 8601 (UTC) or null
                    "weight": None,             # Canvas points aren't a % weight
                    "canvas_id": str(a.get("id")),
                    "canvas_submitted": submitted,
                    "canvas_submission_status": status,
                    "canvas_submitted_at": submitted_at,
                    "canvas_missing": missing,
                    "points_possible": a.get("points_possible"),
                    "score": sub.get("score"),
                }
            )
    return out


def submission_feedback(course_id: int | str, assignment_id: str) -> dict | None:
    """Fetch this student's grader comments and rubric remarks for one assignment.

    Returns None when there's no actionable textual feedback (a bare numeric grade
    with no comments has nothing for the tutor to learn from).
    """
    try:
        sub = _get_one(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self",
            {"include[]": ["submission_comments", "rubric_assessment"]},
        )
    except Exception:
        return None
    if not isinstance(sub, dict):
        return None

    comments = [
        c.get("comment", "").strip()
        for c in (sub.get("submission_comments") or [])
        if isinstance(c, dict) and c.get("comment", "").strip()
    ]
    rubric_comments = [
        crit.get("comments", "").strip()
        for crit in (sub.get("rubric_assessment") or {}).values()
        if isinstance(crit, dict) and crit.get("comments", "").strip()
    ]
    if not comments and not rubric_comments:
        return None

    body = sub.get("body")
    attachments = [
        {
            "name": att.get("display_name") or att.get("filename") or "submission_file",
            "url": att.get("url"),
            "size": att.get("size") or 0,
        }
        for att in (sub.get("attachments") or [])
        if isinstance(att, dict) and att.get("url")
    ]
    return {
        "score": sub.get("score"),
        "grade": sub.get("grade"),
        "comments": comments,
        "rubric_comments": rubric_comments,
        # Text-entry submissions carry the actual answer — trimmed so a long essay
        # doesn't blow the prompt budget; file uploads have no body here.
        "body": _strip_html(body)[:4000] if body else "",
        # File-upload submissions carry attachments instead of a body; the caller
        # downloads and extracts text from these (avoids a canvas -> notes_engine
        # circular import).
        "attachments": attachments,
    }


def _submission_state(assignment: dict) -> tuple[bool, str, str | None, bool]:
    """Return current user's Canvas submission state for an assignment."""
    sub = assignment.get("submission")
    if not isinstance(sub, dict):
        return False, "unsubmitted", None, False

    workflow = str(sub.get("workflow_state") or "").lower()
    submitted_at = sub.get("submitted_at")
    attempt = sub.get("attempt") or 0
    missing = bool(sub.get("missing"))
    has_submission = bool(submitted_at) or workflow in {
        "submitted",
        "graded",
        "pending_review",
        "complete",
    } or attempt > 0

    submitted = has_submission and workflow not in {"unsubmitted", "deleted"} and not missing
    status = workflow or ("submitted" if submitted else "unsubmitted")
    return submitted, status, submitted_at, missing


# Extensions we're willing to list for class knowledge and study processing.
_COURSE_FILE_EXTS = {
    ".pdf", ".ppt", ".pptx", ".key", ".doc", ".docx", ".txt", ".rtf",
    ".md", ".csv", ".html", ".htm", ".json", ".xml", ".yaml", ".yml",
    ".py", ".java", ".c", ".cpp", ".js", ".ts", ".sql", ".r",
}
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


def _file_name(file_meta: dict) -> str:
    """Return a useful name without losing the extension Canvas stores separately.

    Canvas instructors can set a display name such as "Syllabus" while the actual
    filename remains ``syllabus.pdf``. Using only display_name made these files look
    extensionless, so they were silently omitted from Tutor knowledge.
    """
    display = str(file_meta.get("display_name") or "").strip()
    filename = str(file_meta.get("filename") or "").strip()
    if not display:
        return filename
    if "." not in display and "." in filename:
        return display + "." + filename.rsplit(".", 1)[-1]
    return display


def course_files(course_id: int | str) -> list[dict]:
    """List readable course files from Canvas."""
    try:
        items = _get_all(f"/api/v1/courses/{course_id}/files", {"sort": "created_at", "order": "desc"})
    except Exception:
        return []
    out = []
    for f in items:
        if not isinstance(f, dict):
            continue
        name = _file_name(f)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in _COURSE_FILE_EXTS:
            continue
        size = f.get("size") or 0
        if size > _MAX_FILE_BYTES:
            continue
        out.append(
            {
                "canvas_file_id": str(f["id"]),
                "name": name,
                "url": f.get("url") or f.get("download_url") or "",
                "size": size,
                "updated_at": f.get("updated_at") or f.get("created_at") or "",
            }
        )
    return out


def slide_files() -> list[dict]:
    """All slide-like files across active courses."""
    result = []
    for c in active_courses():
        for f in course_files(c["id"]):
            result.append({**f, "course": c["code"], "course_name": c["name"]})
    return result


def _get_one(path: str, params: dict | None = None) -> dict:
    """GET a single Canvas resource (not a collection)."""
    import httpx
    cfg     = load_config()
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = _get_with_retry(client, cfg["base_url"].rstrip("/") + path, headers=headers, params=params or {})
        resp.raise_for_status()
        return resp.json()


def _strip_html(html_str: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    from html.parser import HTMLParser
    import html as _html

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
        def handle_data(self, data: str):
            self._parts.append(data)
        def get_text(self) -> str:
            return "\n".join(p.strip() for p in self._parts if p.strip())

    s = _Stripper()
    s.feed(_html.unescape(html_str or ""))
    return s.get_text()


class _LinkCollector:
    """Small HTML link collector for Canvas rich-text fields."""

    def __init__(self):
        from html.parser import HTMLParser

        collector = self

        class Parser(HTMLParser):
            def handle_starttag(self, tag: str, attrs):
                if tag.lower() != "a":
                    return
                data = {k: v for k, v in attrs if k and v}
                collector.links.append(data)

        self.links: list[dict] = []
        self.parser = Parser()


def _file_refs_from_html(html_str: str) -> list[dict]:
    """Return Canvas file refs linked from a rich-text HTML block."""
    if not html_str:
        return []
    collector = _LinkCollector()
    collector.parser.feed(html_str)

    refs: list[dict] = []
    seen: set[str] = set()
    for link in collector.links:
        joined = " ".join(
            link.get(k, "") for k in ("data-api-endpoint", "href", "title")
        )
        match = re.search(r"(?:/api/v1)?/courses/\d+/files/(\d+)|/files/(\d+)", joined)
        if not match:
            continue
        file_id = match.group(1) or match.group(2)
        if file_id in seen:
            continue
        seen.add(file_id)
        name = link.get("title") or unquote((link.get("href") or "").rsplit("/", 1)[-1])
        refs.append({"canvas_file_id": file_id, "name": name or f"Canvas file {file_id}"})
    return refs


def _metadata_for_file(file_id: str, course_code: str, course_name: str = "") -> dict:
    """Fetch Canvas file metadata and normalize it like course_files()."""
    meta = _get_one(f"/api/v1/files/{file_id}")
    name = _file_name(meta) or f"Canvas file {file_id}"
    return {
        "canvas_file_id": str(meta.get("id") or file_id),
        "name": name,
        "url": meta.get("url") or meta.get("download_url") or "",
        "size": meta.get("size") or 0,
        "updated_at": meta.get("updated_at") or meta.get("created_at") or "",
        "course": course_code,
        "course_name": course_name,
    }


def linked_course_files(course_id: int | str, course_code: str, course_name: str = "") -> list[dict]:
    """Return Canvas files linked from syllabus/pages/announcements/assignments."""
    refs: list[dict] = []

    def add_refs(html_body: str | None) -> None:
        refs.extend(_file_refs_from_html(html_body or ""))

    try:
        course = _get_one(f"/api/v1/courses/{course_id}", {"include[]": "syllabus_body"})
        add_refs(course.get("syllabus_body"))
    except Exception:
        pass

    try:
        pages = _get_all(f"/api/v1/courses/{course_id}/pages", {"published": "true"})
        for p in pages:
            if not isinstance(p, dict) or not p.get("url"):
                continue
            try:
                page = _get_one(f"/api/v1/courses/{course_id}/pages/{p['url']}")
                add_refs(page.get("body"))
            except Exception:
                continue
    except Exception:
        pass

    try:
        announcements = _get_all(
            f"/api/v1/courses/{course_id}/discussion_topics",
            {"only_announcements": "true"},
        )
        for a in announcements:
            if isinstance(a, dict):
                add_refs(a.get("message"))
    except Exception:
        pass

    try:
        assignments_raw = _get_all(f"/api/v1/courses/{course_id}/assignments", {})
        for a in assignments_raw:
            if isinstance(a, dict):
                add_refs(a.get("description"))
    except Exception:
        pass

    out: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        file_id = ref["canvas_file_id"]
        if file_id in seen:
            continue
        seen.add(file_id)
        try:
            out.append(_metadata_for_file(file_id, course_code, course_name))
        except Exception:
            out.append({
                "canvas_file_id": file_id,
                "name": ref["name"],
                "url": "",
                "size": 0,
                "updated_at": "",
                "course": course_code,
                "course_name": course_name,
            })
    return out


def fetch_course_knowledge(course_id: int | str, course_code: str) -> str:
    """Fetch ALL text content from a Canvas course for the tutor.

    Includes: syllabus, all course pages, announcements, and assignment descriptions.
    Returns a single formatted string ready for the system prompt.
    """
    sections: list[str] = [f"COMPLETE COURSE KNOWLEDGE — {course_code}"]

    # ── Syllabus ──────────────────────────────────────────────────────────────
    try:
        course = _get_one(f"/api/v1/courses/{course_id}", {"include[]": "syllabus_body"})
        syllabus = _strip_html(course.get("syllabus_body") or "")
        if syllabus:
            sections.append(f"=== SYLLABUS ===\n{syllabus}")
    except Exception:
        pass

    # ── Course pages ──────────────────────────────────────────────────────────
    try:
        pages = _get_all(f"/api/v1/courses/{course_id}/pages", {"published": "true"})
        page_texts: list[str] = []
        import httpx
        cfg     = load_config()
        headers = {"Authorization": f"Bearer {cfg['token']}"}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for p in pages:
                if not isinstance(p, dict):
                    continue
                try:
                    r    = client.get(
                        f"{cfg['base_url'].rstrip('/')}/api/v1/courses/{course_id}/pages/{p['url']}",
                        headers=headers,
                    )
                    body = _strip_html(r.json().get("body") or "")
                    if body:
                        page_texts.append(f"--- {p.get('title', 'Untitled')} ---\n{body}")
                except Exception:
                    continue
        if page_texts:
            sections.append("=== COURSE PAGES ===\n" + "\n\n".join(page_texts))
    except Exception:
        pass

    # ── Announcements ─────────────────────────────────────────────────────────
    try:
        announcements = _get_all(
            f"/api/v1/courses/{course_id}/discussion_topics",
            {"only_announcements": "true"},
        )
        ann_texts: list[str] = []
        for a in announcements[:15]:
            if not isinstance(a, dict):
                continue
            body = _strip_html(a.get("message") or "")
            if body:
                posted = (a.get("posted_at") or "")[:10]
                ann_texts.append(f"--- {a.get('title', '')} ({posted}) ---\n{body}")
        if ann_texts:
            sections.append("=== ANNOUNCEMENTS ===\n" + "\n\n".join(ann_texts))
    except Exception:
        pass

    # ── Assignment descriptions ───────────────────────────────────────────────
    try:
        assignments = _get_all(f"/api/v1/courses/{course_id}/assignments", {})
        asgn_texts: list[str] = []
        for a in assignments:
            if not isinstance(a, dict):
                continue
            desc = _strip_html(a.get("description") or "")
            if desc:
                due  = (a.get("due_at") or "no due date")[:10]
                asgn_texts.append(f"--- {a.get('name', '')} (due {due}) ---\n{desc}")
        if asgn_texts:
            sections.append("=== ASSIGNMENT DETAILS ===\n" + "\n\n".join(asgn_texts))
    except Exception:
        pass

    return "\n\n".join(sections)


def download_file(url: str) -> bytes:
    """Download a Canvas file by its URL."""
    import httpx

    cfg = load_config()
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.content


def download_file_by_id(canvas_file_id: str) -> bytes:
    """Re-fetch fresh file metadata to get a non-expired URL, then download.

    The URLs returned by the files listing API are pre-signed S3 URLs that
    expire after a few minutes. Calling GET /api/v1/files/:id returns a fresh
    pre-signed URL each time, avoiding 403/404 errors on a long processing queue.
    """
    import httpx
    import time

    cfg = load_config()
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                # Step 1: get fresh metadata (includes a fresh pre-signed download URL)
                meta = client.get(
                    f"{cfg['base_url'].rstrip('/')}/api/v1/files/{canvas_file_id}",
                    headers=headers,
                )
                meta.raise_for_status()
                fresh_url = meta.json().get("url") or meta.json().get("download_url")
                if not fresh_url:
                    raise ValueError(f"No download URL in metadata for file {canvas_file_id}")
                # Step 2: download using the fresh URL
                dl = client.get(fresh_url, headers=headers)
                dl.raise_for_status()
                return dl.content
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error or RuntimeError(f"download failed for file {canvas_file_id}")
