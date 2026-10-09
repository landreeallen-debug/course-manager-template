"""Shared note-generation engine used by the Classes page.

All Claude API calls for classifying, note-writing, and cheat-sheet updates live
here so they can be imported without pulling in Streamlit.
"""
from __future__ import annotations

import base64
import os
import re

import anthropic
from dotenv import load_dotenv

import canvas
import study_notes_store as store

load_dotenv()

AUTO_MAX_BYTES = 20 * 1024 * 1024  # skip files > 20 MB

_CLIENT = anthropic.Anthropic()

# ── Prompts ───────────────────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """\
Read this document and classify it as exactly one of:
  slides    — lecture slides, course notes, or any document meant to teach content
  homework  — a problem set, assignment, or worksheet the student must complete
  skip      — answer keys, syllabi, administrative files, or anything with no study value

Reply with a single word: slides, homework, or skip.
"""

_NOTES_PROMPT = """\
You are a student taking notes in {course}.

Attached are the lecture slides for one class session. Write thorough, organized
notes for this lecture only — as if you were sitting in class writing them down.
Capture every important definition, concept, rule, example, and diagram.
Be detailed and clear.

Use ONLY what is explicitly present on the slides. Do not add outside knowledge,
textbook definitions, general domain knowledge, or anything from prior training —
even if it seems standard or correct. Do not infer, extrapolate, or fill gaps with
anything not directly stated or shown on the slides. If a slide is unclear or
incomplete, capture it as-is rather than completing it with outside information.

Format:
## [Lecture Topic] — [Date if visible on slides]

[your notes here]
"""

_CHEAT_SLIDES_PROMPT = """\
Extract cheat-sheet entries from these {course} lecture slides.

Use ONLY what is explicitly present on the slides. Do not add outside knowledge,
textbook definitions, general domain knowledge, or anything from prior training —
even if it seems standard or correct. Do not infer, extrapolate, or fill gaps with
anything not directly stated or shown on the slides. Every example must come from
the slides — do not invent a new one, even to illustrate the rule more clearly.

Start your response IMMEDIATELY with the first ## header. No introduction, no preamble,
no sentences like "I'll add..." or "Here are the entries". Just the entries.

For every process, method, symbol, or rule a student needs to know from these slides:

## [Exact Name]
**Use when**: ...
**How**: (numbered steps if it's a procedure; one line if simple)
**Example**: ...
**Watch out for**: ...

If the slides contain nothing worth adding as a cheat-sheet entry, output exactly: NOTHING_NEW
"""

_CHEAT_FROM_NOTES_PROMPT = """\
Extract cheat-sheet entries from these {course} class notes.

Use ONLY what is explicitly present in these notes. Do not add outside knowledge,
textbook definitions, general domain knowledge, or anything from prior training —
even if it seems standard or correct. Do not infer, extrapolate, or fill gaps with
anything not directly stated in the notes. Every example must come from the notes —
do not invent a new one, even to illustrate the rule more clearly.

Start your response IMMEDIATELY with the first ## header. No introduction or preamble.

For every process, method, symbol, or rule a student needs to know:

## [Exact Name]
**Use when**: ...
**How**: (numbered steps if it's a procedure; one line if simple)
**Example**: ...
**Watch out for**: ...
"""

_CHEAT_HOMEWORK_PROMPT = """\
A {course} homework assignment is attached. You also have:
  • CLASS NOTES — everything covered in lecture
  • PRACTICE MATERIALS — similar solved problems and answer keys (if available)

CLASS NOTES:
{class_notes}

{practice_section}

Your job: write a reference guide that teaches the student HOW to solve each problem
AND gives the actual answer for each one.

Rules:
- For each question/section: explain the concept, give the relevant slide content, show
  the METHOD step-by-step applied directly to the actual homework question, and if a
  similar solved problem exists in the practice materials reference it explicitly
  ("cf. Practice Q3 — same approach").
- Solve the actual homework question directly — don't substitute a different example.
- End each section with the final answer, formatted exactly as it would be entered on
  the assignment.

Start IMMEDIATELY with the ## header. No preamble or commentary.

## {hw_name} — Reference Guide

### [Section/Question label]
**Concept**: [exact linguistic concept being tested]
**What you need from the slides**: [quote or paraphrase the key rule, definition, or table]
**Method** (step-by-step, applied to this exact question):
1. ...
2. ...
**Practice parallel** (if applicable): [cite a similar practice problem and its solution]
**Answer**: [the final answer, formatted exactly as entered on the assignment]

[repeat for each section]
"""

_MISTAKE_PROMPT = """\
You are reviewing grader feedback (and, when available, the student's actual
submitted answer or file) from one {course} assignment to build a running
"repeated mistakes" reference so the student stops making the same errors.

Assignment: {title}
Score: {score_line}

GRADER FEEDBACK AND SUBMISSION:
{feedback}

If this identifies a genuine logic error, misconception, or recurring mistake
pattern (not a one-off typo, missed formatting rule, or late penalty), write
ONE entry. If a submitted answer/file is included, ground "What happened" in
the specific line, step, or passage where the error occurred, not just the
grader's paraphrase:

## [Short name for the mistake, e.g. "Off-by-one in loop bounds"]
**What happened**: [what the student did, citing the specific spot if the submission is available]
**Why it's wrong**: [the underlying misconception]
**Catch it next time**: [a concrete, specific check to run before submitting]

If the feedback is purely positive, or too vague/minor to generalize into a
reusable lesson, output exactly: NOTHING_NEW
"""

# ── API helpers ───────────────────────────────────────────────────────────────

def _doc_block(file_bytes: bytes, file_name: str) -> dict:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    media_type = {
        "pdf":  "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ppt":  "application/vnd.ms-powerpoint",
    }.get(ext, "application/octet-stream")
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(file_bytes).decode(),
        },
    }


def _pdf_to_text(file_bytes: bytes) -> str:
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    chunks = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(f"[Page {i + 1}]\n{text}")
    return "\n\n".join(chunks)


def _pdf_has_images(file_bytes: bytes) -> bool:
    """True if any page embeds an image — e.g. a data table rendered as a graphic.

    pypdf's extract_text() only reads real text objects, so a page can return
    plenty of text (instructions, prose) while the one image on it — the actual
    table students need — is silently skipped.
    """
    import io
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        return any(page.images for page in reader.pages)
    except Exception:
        return False


def _image_block(image_bytes: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(image_bytes).decode(),
        },
    }


def _api_call(
    file_bytes: bytes,
    file_name: str,
    prompt: str,
    model: str,
    max_tokens: int,
    extra_blocks: list | None = None,
) -> str:
    """Send a file to Claude, optionally with extra content blocks (e.g. textbook images).

    Falls back to text extraction if the PDF exceeds 100 pages.
    """
    def _stream(messages) -> str:
        with _CLIENT.messages.stream(model=model, max_tokens=max_tokens, messages=messages) as s:
            return s.get_final_text()

    def _build_content(doc_block):
        content = [doc_block]
        if extra_blocks:
            content.extend(extra_blocks)
        content.append({"type": "text", "text": prompt})
        return content

    try:
        return _stream([{"role": "user", "content": _build_content(_doc_block(file_bytes, file_name))}])
    except anthropic.BadRequestError as e:
        if "100 PDF pages" not in str(e):
            raise
        extracted  = _pdf_to_text(file_bytes)
        text_block = {"type": "text", "text": f"[Document: {file_name}]\n\n{extracted}"}
        return _stream([{"role": "user", "content": _build_content(text_block)}])


# ── Public functions ──────────────────────────────────────────────────────────

def classify(file_bytes: bytes, file_name: str) -> str:
    raw = _api_call(file_bytes, file_name, _CLASSIFY_PROMPT, "claude-haiku-4-5", 10).lower()
    if "slide" in raw or "lecture" in raw:
        return "slides"
    if "homework" in raw or "assignment" in raw:
        return "homework"
    return "skip"


_HOMEWORK_RE = re.compile(
    r"(?<![a-z])(?:"
    r"h\.?w\.?\s*[_-]?\s*\d*[a-z]?|"
    r"home\s*work|"
    r"assign(?:ments?|ed)?|"
    r"worksheet|"
    r"problem\s*set|"
    r"p\s*set|"
    r"practice|"
    r"ex(?:ercises?|s)?\.?\s*\d+"
    r")(?![a-z])"
)
_HOMEWORK_SKIP_RE = re.compile(
    r"(?<![a-z])(?:answer|answers|answer\s*key|key|solutions?|soln|rubric|optional)(?![a-z])"
)


def _homework_scan_text(name: str) -> str:
    return name.rsplit(".", 1)[0].lower().replace("_", " ")


def _is_homework(name: str) -> bool:
    """True for homework-like files, False for answer keys and reference-only files."""
    text = _homework_scan_text(name)
    return bool(_HOMEWORK_RE.search(text)) and not _HOMEWORK_SKIP_RE.search(text)


def _normalize_classification(file_name: str, classification: str) -> str:
    """Use strong filename signals to correct common homework/answer-key mistakes."""
    text = _homework_scan_text(file_name)
    if _HOMEWORK_SKIP_RE.search(text):
        return "skip"
    if _is_homework(file_name):
        return "homework"
    return classification if classification in {"slides", "homework", "skip"} else "skip"


def needs_processing(f: dict) -> bool:
    """Return True when a Canvas file should be reviewed by the notes pipeline."""
    processed = store.get_processed(f["canvas_file_id"])
    if processed is None:
        return True
    return processed.get("classification") == "skip" and _is_homework(f.get("name", ""))


def update_notes(file_bytes: bytes, file_name: str, course: str) -> str:
    """Generate notes for just this lecture, then prepend to the existing document.

    Generating one lecture at a time (instead of regenerating the whole doc) means
    max_tokens is never a bottleneck no matter how many lectures accumulate.
    """
    new_section = _api_call(file_bytes, file_name, _NOTES_PROMPT.format(course=course), "claude-opus-4-8", 16000)

    header = f"# {course} — Class Notes\n\n"
    existing = store.get_course_notes(course)
    if existing and existing["content"]:
        body = existing["content"]
        if body.startswith(header):
            body = body[len(header):]
        return header + body + "\n\n" + new_section
    return header + new_section


def _strip_preamble(text: str) -> str:
    """Remove any lines before the first ## or # header — stops Claude commentary polluting the doc."""
    for i, line in enumerate(text.split("\n")):
        if line.startswith("#"):
            return "\n".join(text.split("\n")[i:]).strip()
    return text.strip()


def _append_to_sheet(course: str, new_section: str) -> str:
    """Append a clean new section to the existing cheat sheet."""
    header   = f"# {course} — Process & Method Cheat Sheet\n\n"
    existing = store.get_cheat_sheet(course)
    current  = existing["content"] if existing else ""
    cleaned  = _strip_preamble(new_section)
    if not cleaned or "NOTHING_NEW" in cleaned.upper():
        return current if current else header
    if current:
        body = current[len(header):] if current.startswith(header) else current
        return header + body + "\n\n---\n\n" + cleaned
    return header + cleaned


_PLAIN_TEXT_EXTS = {
    "txt", "rtf", "md", "csv", "tex", "json", "xml", "yaml", "yml",
    # Source code — common for CS homework submissions.
    "py", "java", "c", "cpp", "cc", "h", "hpp", "js", "ts", "jsx", "tsx",
    "css", "html", "htm", "sql", "r", "rb", "go", "rs", "swift", "kt", "sh",
}


def extract_text(file_bytes: bytes, file_name: str) -> str:
    """Extract plain text from a readable course file."""
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext == "pdf":
        return _pdf_to_text(file_bytes)
    if ext in _PLAIN_TEXT_EXTS:
        return file_bytes.decode("utf-8", errors="ignore")
    if ext == "docx":
        return _office_zip_to_text(file_bytes, ("word/document.xml",))
    if ext == "pptx":
        return _office_zip_to_text(file_bytes, ("ppt/slides/slide",))
    return ""


def _office_zip_to_text(file_bytes: bytes, wanted_prefixes: tuple[str, ...]) -> str:
    """Extract text nodes from docx/pptx Office XML files."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    chunks: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            names = sorted(
                n for n in zf.namelist()
                if n.endswith(".xml") and any(n.startswith(p) for p in wanted_prefixes)
            )
            for name in names:
                try:
                    root = ET.fromstring(zf.read(name))
                except Exception:
                    continue
                texts = [
                    node.text.strip()
                    for node in root.iter()
                    if node.text and node.text.strip()
                ]
                if texts:
                    chunks.append(f"[{name}]\n" + "\n".join(texts))
    except Exception:
        return ""
    return "\n\n".join(chunks)


def _clip_material(text: str, limit: int = 18000) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[truncated]"


_CRITICAL_COURSE_FACT_RE = re.compile(
    r"\b(?:midterm|final\s+exam|course schedule|office hours?)\b",
    re.I,
)
_SECONDARY_COURSE_FACT_RE = re.compile(
    r"\b(?:exam|quiz|test|grading|grade|attendance|due|homework|assignment|project)\b",
    re.I,
)


def _course_fact_snippets(course: str, max_chars: int = 14000) -> str:
    """Pull high-value logistics snippets to the front of tutor context."""
    sources: list[tuple[str, str]] = []
    for material in store.list_course_materials(course):
        if material.get("content"):
            sources.append((f"Posted file: {material['file_name']}", material["content"]))
    knowledge = store.get_course_knowledge(course)
    if knowledge and knowledge.get("content"):
        sources.append(("Canvas course info", knowledge["content"]))

    snippets: list[str] = []
    seen: set[str] = set()

    def collect(pattern: re.Pattern) -> None:
        for label, text in sources:
            lines = [line.strip() for line in text.splitlines()]
            for i, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                center_key = re.sub(r"\s+", " ", f"{label}:{line}".lower())
                if center_key in seen:
                    continue
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet = "\n".join(l for l in lines[start:end] if l)
                if not snippet:
                    continue
                seen.add(center_key)
                snippets.append(f"## {label}\n{snippet}")
                if sum(len(s) for s in snippets) >= max_chars:
                    return

    collect(_CRITICAL_COURSE_FACT_RE)
    if sum(len(s) for s in snippets) < max_chars:
        collect(_SECONDARY_COURSE_FACT_RE)

    if not snippets:
        return ""
    return "# High-Priority Course Logistics\n\n" + "\n\n".join(snippets)


def sync_course_materials(files: list[dict], max_bytes: int = 12 * 1024 * 1024) -> tuple[int, int]:
    """Extract and store readable text from posted Canvas files.

    Returns (added_or_updated, skipped).
    """
    added, skipped = 0, 0
    for f in files:
        file_id = str(f.get("canvas_file_id") or "")
        if not file_id:
            skipped += 1
            continue
        existing = store.get_course_material(file_id)
        if existing and existing.get("file_updated_at") == f.get("updated_at"):
            continue
        if (f.get("size") or 0) > max_bytes:
            skipped += 1
            continue
        try:
            file_bytes = canvas.download_file_by_id(file_id)
            file_name = f.get("name", "")
            text = extract_text(file_bytes, file_name)
            # Image-only/scanned PDFs have no embedded text for pypdf, and a page can
            # mix real text (instructions, prose) with a table rendered as a graphic —
            # pypdf silently drops just the image part. Either case needs the
            # document-capable model to transcribe the actual page images instead.
            if file_name.lower().endswith(".pdf") and (not text.strip() or _pdf_has_images(file_bytes)):
                text = _api_call(
                    file_bytes,
                    file_name,
                    "Extract all readable text from this document faithfully, including any tables, "
                    "data grids, or charts rendered as images — transcribe every row/column exactly as "
                    "shown. Preserve headings, dates, grading rules, schedules, and contact information. "
                    "Do not summarize.",
                    "claude-haiku-4-5",
                    12000,
                )
        except Exception:
            skipped += 1
            continue
        if not text.strip():
            skipped += 1
            continue
        store.upsert_course_material(
            file_id,
            f.get("course", ""),
            f.get("name", ""),
            _clip_material(text),
            f.get("updated_at"),
        )
        added += 1
    return added, skipped


def update_mistakes(course: str, title: str, score_line: str, feedback: str) -> str:
    """Extract a reusable lesson from one piece of graded feedback and append it.

    Mirrors _append_to_sheet's incremental-append pattern: a cheap Haiku call turns
    raw grader comments into a short, generalizable lesson (or NOTHING_NEW).
    """
    prompt = _MISTAKE_PROMPT.format(course=course, title=title, score_line=score_line, feedback=feedback)
    with _CLIENT.messages.stream(
        model="claude-haiku-4-5", max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    ) as s:
        raw = s.get_final_text()

    header = f"# {course} — Recurring Mistakes to Watch For\n\n"
    existing = store.get_mistake_notes(course)
    current = existing["content"] if existing else ""
    cleaned = _strip_preamble(raw)
    if not cleaned or "NOTHING_NEW" in cleaned.upper():
        return current if current else header
    if current:
        body = current[len(header):] if current.startswith(header) else current
        return header + body + "\n\n---\n\n" + cleaned
    return header + cleaned


_SUBMISSION_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per attachment
_SUBMISSION_TEXT_BUDGET = 8000  # combined chars across a submission's attachments


def _extract_submission_text(attachments: list[dict]) -> str:
    """Download and extract text from graded-submission attachments, best-effort.

    Skips oversized or unreadable files rather than failing the whole sync —
    grader comments alone are still useful even if the file itself can't be read.
    """
    parts: list[str] = []
    used = 0
    for att in attachments:
        if used >= _SUBMISSION_TEXT_BUDGET:
            break
        if (att.get("size") or 0) > _SUBMISSION_FILE_MAX_BYTES or not att.get("url"):
            continue
        try:
            file_bytes = canvas.download_file(att["url"])
            text = extract_text(file_bytes, att.get("name", "")).strip()
        except Exception:
            continue
        if not text:
            continue
        clipped = text[: _SUBMISSION_TEXT_BUDGET - used]
        parts.append(f"[{att.get('name', 'submitted file')}]\n{clipped}")
        used += len(clipped)
    return "\n\n".join(parts)


def sync_graded_work(canvas_assignments: list[dict]) -> tuple[int, int]:
    """Pull grader feedback for newly-graded assignments and extract mistake patterns.

    Skips assignments already analyzed at their current score, and any grade with
    no textual feedback to learn from. Returns (lessons_added, skipped).
    """
    added, skipped = 0, 0
    for a in canvas_assignments:
        if a.get("canvas_submission_status") != "graded":
            continue
        canvas_id = a.get("canvas_id")
        course = a.get("course", "")
        if not canvas_id or not course:
            skipped += 1
            continue

        score = a.get("score")
        existing = store.get_graded_work(canvas_id)
        if existing and existing.get("score") == score:
            continue  # already analyzed at this grade

        try:
            feedback = canvas.submission_feedback(a["course_id"], canvas_id)
        except Exception:
            skipped += 1
            continue

        if feedback:
            points_possible = a.get("points_possible")
            if feedback.get("score") is not None and points_possible is not None:
                score_line = f"{feedback['score']}/{points_possible}"
            else:
                score_line = feedback.get("grade") or "ungraded"

            lines = [f"- {c}" for c in feedback["comments"] + feedback["rubric_comments"]]
            if feedback.get("body"):
                lines.append(f"\nSTUDENT'S SUBMITTED ANSWER (may be truncated):\n{feedback['body']}")
            submission_text = _extract_submission_text(feedback.get("attachments") or [])
            if submission_text:
                lines.append(f"\nSTUDENT'S SUBMITTED FILE (may be truncated):\n{submission_text}")
            feedback_text = "\n".join(lines)

            try:
                content = update_mistakes(course, a.get("title", ""), score_line, feedback_text)
                store.upsert_mistake_notes(course, content)
                added += 1
            except Exception:
                skipped += 1

        store.mark_graded_processed(canvas_id, course, a.get("title", ""), score)
    return added, skipped


_IMG_TYPES = {
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
    "gif":  "image/gif",
}


def load_textbook_images(course: str) -> list[dict]:
    """Load stored textbook page images for a course as content blocks.

    Returns a list of {"label": str, "block": dict} ready to pass to _api_call.
    Missing or unreadable files are silently skipped.
    """
    from pathlib import Path
    pages  = store.list_textbook_pages(course)
    result = []
    for p in pages:
        path = Path(p["file_path"])
        if not path.exists():
            continue
        ext = path.suffix.lstrip(".").lower()
        mt  = _IMG_TYPES.get(ext)
        if not mt:
            continue
        try:
            result.append({"label": p["label"], "block": _image_block(path.read_bytes(), mt)})
        except Exception:
            pass
    return result


def update_cheat_sheet(
    file_bytes: bytes,
    file_name: str,
    course: str,
    doc_type: str,
    practice_texts: list[tuple[str, str]] | None = None,
) -> str:
    """Generate a cheat-sheet section and append it.

    practice_texts: list of (label, text) pairs — answer keys or practice materials
    to cross-reference when building the homework guide.
    """
    if doc_type == "homework assignment":
        notes_row   = store.get_course_notes(course)
        class_notes = notes_row["content"] if notes_row else "(No class notes yet.)"
        hw_name     = file_name.rsplit(".", 1)[0]

        if practice_texts:
            blocks = "\n\n".join(
                f"=== {label} ===\n{text}" for label, text in practice_texts if text.strip()
            )
            practice_section = f"PRACTICE MATERIALS & ANSWER KEYS:\n{blocks}"
        else:
            practice_section = ""

        textbook_imgs = load_textbook_images(course)
        extra_blocks: list = []
        if textbook_imgs:
            extra_blocks.append({
                "type": "text",
                "text": f"TEXTBOOK PAGES ({len(textbook_imgs)} uploaded — use these alongside class notes):",
            })
            for img in textbook_imgs:
                extra_blocks.append({"type": "text", "text": f"[{img['label']}]"})
                extra_blocks.append(img["block"])

        prompt = _CHEAT_HOMEWORK_PROMPT.format(
            course=course, class_notes=class_notes,
            hw_name=hw_name, practice_section=practice_section,
        )
        new_section = _api_call(
            file_bytes, file_name, prompt, "claude-opus-4-8", 16000,
            extra_blocks=extra_blocks or None,
        )
    else:
        prompt      = _CHEAT_SLIDES_PROMPT.format(course=course)
        new_section = _api_call(file_bytes, file_name, prompt, "claude-opus-4-8", 8000)

    return _append_to_sheet(course, new_section)


def rebuild_cheat_from_notes(course: str) -> str:
    """Rebuild the slides portion of the cheat sheet from already-generated class notes.

    Much faster than re-downloading all PDFs — reads the stored class notes and
    extracts cheat-sheet entries from them. Called after a corruption or reset.
    """
    notes_row = store.get_course_notes(course)
    if not notes_row:
        return f"# {course} — Process & Method Cheat Sheet\n\n"

    prompt  = _CHEAT_FROM_NOTES_PROMPT.format(course=course)
    content = notes_row["content"]

    with _CLIENT.messages.stream(
        model="claude-opus-4-8", max_tokens=16000,
        messages=[{"role": "user", "content": f"{prompt}\n\nCLASS NOTES:\n{content}"}],
    ) as s:
        raw = s.get_final_text()

    header = f"# {course} — Process & Method Cheat Sheet\n\n"
    return header + _strip_preamble(raw)


def sort_pending(files: list[dict]) -> list[dict]:
    """Slides before homework — enforces correct processing order regardless of Canvas upload time."""
    return sorted(files, key=lambda f: 2 if _is_homework(f["name"]) else 1)


def process_file(f: dict) -> str:
    """Download, classify, and generate notes for one Canvas file. Returns a result string."""
    size = f.get("size") or 0
    if size > AUTO_MAX_BYTES:
        store.mark_processed(f["canvas_file_id"], f["course"], f["name"], "skip", f.get("updated_at"))
        return f"[skip] **{f['name']}** - too large, skipped"

    try:
        file_bytes = canvas.download_file_by_id(f["canvas_file_id"])
    except Exception as e:
        return f"[error] **{f['name']}** - download failed: {e}"

    try:
        classification = classify(file_bytes, f["name"])
        classification = _normalize_classification(f["name"], classification)
    except Exception as e:
        return f"[error] **{f['name']}** - could not read: {e}"

    course = f["course"]

    if classification == "skip":
        store.mark_processed(f["canvas_file_id"], course, f["name"], "skip", f.get("updated_at"))
        return f"[skip] **{f['name']}** - skipped (not slides or homework)"

    if classification == "slides":
        try:
            store.upsert_course_notes(course, update_notes(file_bytes, f["name"], course))
        except Exception as e:
            return f"[error] **{f['name']}** - notes failed: {e}"

    try:
        doc_type = "lecture slides" if classification == "slides" else "homework assignment"
        store.upsert_cheat_sheet(course, update_cheat_sheet(file_bytes, f["name"], course, doc_type))
    except Exception as e:
        return f"[error] **{f['name']}** - cheat sheet failed: {e}"

    store.mark_processed(f["canvas_file_id"], course, f["name"], classification, f.get("updated_at"))
    if classification == "slides":
        return f"[notes] **{f['name']}** - added to notes + cheat sheet"
    return f"[homework] **{f['name']}** - homework processes added to cheat sheet"


_STOPWORDS = {
    "about", "after", "again", "also", "and", "any", "are", "because", "before",
    "can", "class", "course", "did", "does", "for", "from", "get", "have",
    "how", "into", "its", "just", "like", "me", "my", "not", "of", "on",
    "or", "our", "the", "this", "to", "was", "what", "when", "where", "who",
    "why", "with", "you", "your",
}


def has_tutor_sources(course: str) -> bool:
    """Whether the tutor has any stored course context beyond live assignment names."""
    return bool(
        store.get_course_knowledge(course)
        or store.list_course_materials(course)
        or store.get_course_notes(course)
        or store.get_cheat_sheet(course)
    )


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", text.lower())
        if t not in _STOPWORDS
    }


def _source_documents(course: str) -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    k = store.get_course_knowledge(course)
    if k and k.get("content"):
        docs.append((f"Course information, syllabus, pages, announcements — {course}", k["content"]))
    for m in store.list_course_materials(course):
        if m.get("content"):
            docs.append((f"Posted file: {m['file_name']}", m["content"]))
    n = store.get_course_notes(course)
    if n and n.get("content"):
        docs.append((f"Class notes — {course}", n["content"]))
    s = store.get_cheat_sheet(course)
    if s and s.get("content"):
        docs.append((f"Process and homework cheat sheet — {course}", s["content"]))
    return docs


def _chunks(title: str, text: str, target: int = 2600, overlap: int = 350) -> list[dict]:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= target:
        return [{"title": title, "text": cleaned}]

    out: list[dict] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + target)
        if end < len(cleaned):
            boundary = cleaned.rfind("\n\n", start, end)
            if boundary > start + target // 2:
                end = boundary
        chunk = cleaned[start:end].strip()
        if chunk:
            out.append({"title": title, "text": chunk})
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return out


_ASSIGNMENT_NUM_RE = re.compile(
    r"\b(?:hw|homework|assignment|hmwk|pset|problem\s*set|quiz|exam|midterm)\s*#?\s*(\d+)\b", re.I
)


def _score_chunk(query: str, query_tokens: set[str], chunk: dict) -> int:
    text = chunk["text"].lower()
    title = chunk["title"].lower()
    score = 0
    for token in query_tokens:
        hits = text.count(token)
        if hits:
            score += min(hits, 8) * 4
        if token in title:
            score += 8

    query_norm = query.lower().strip()
    if query_norm and query_norm in text:
        score += 60
    if any(t in query_tokens for t in {"midterm", "final", "exam", "office", "hours", "schedule"}):
        if _CRITICAL_COURSE_FACT_RE.search(text):
            score += 35
        if "syllabus" in title or "course information" in title:
            score += 20

    # "Homework 4" and a file named HW4.pdf share no substring — the generic
    # keyword scoring above can't connect them, so a vague "help with homework
    # 4" query can lose to unrelated chunks that just repeat the word
    # "homework" more often (e.g. a cheat sheet listing every assignment).
    # Match assignment numbers explicitly so the actual source file always wins.
    q_nums = set(_ASSIGNMENT_NUM_RE.findall(query))
    if q_nums and q_nums & set(re.findall(r"\d+", title)):
        score += 100
    return score


_POSTED_FILE_PREFIX = "Posted file: "


def _relevant_context(course: str, query: str | None, max_chars: int = 46000) -> str:
    docs = _source_documents(course)
    if not docs:
        return ""

    if not query:
        titles = "\n".join(f"- {title}" for title, _ in docs)
        return "# Available Tutor Sources\n\n" + titles

    # Posted homework files are short, self-contained assignments — leaving their
    # coverage to keyword-chunk scoring means a mid-document section (e.g. a table
    # transcribed from an image) can lose out to unrelated chunks and go missing
    # from a student's perspective even though it's in the knowledge base. Always
    # include these whole instead of gambling on relevance scoring.
    homework_docs, other_docs = [], []
    for title, text in docs:
        name = title[len(_POSTED_FILE_PREFIX):] if title.startswith(_POSTED_FILE_PREFIX) else ""
        (homework_docs if name and _is_homework(name) else other_docs).append((title, text))

    parts: list[str] = []
    used = 0
    for title, text in homework_docs:
        block = _clip_source(title, text, 20000)
        if not block:
            continue
        parts.append(block)
        used += len(block)

    remaining = max(max_chars - used, 8000)

    query_tokens = _tokens(query)
    if not query_tokens:
        query_tokens = _tokens("syllabus assignments notes homework")

    scored: list[tuple[int, int, dict]] = []
    order = 0
    for title, text in other_docs:
        for chunk in _chunks(title, text):
            score = _score_chunk(query, query_tokens, chunk)
            if score > 0:
                scored.append((score, order, chunk))
            order += 1

    if not scored:
        for title, text in other_docs[:4]:
            for chunk in _chunks(title, text)[:1]:
                scored.append((1, order, chunk))
                order += 1

    chunk_parts: list[str] = []
    chunk_used = 0
    for _, _, chunk in sorted(scored, key=lambda item: (-item[0], item[1])):
        block = f"## {chunk['title']}\n{chunk['text']}"
        if chunk_used + len(block) > remaining and chunk_parts:
            continue
        chunk_parts.append(block)
        chunk_used += len(block)
        if chunk_used >= remaining:
            break
    parts.extend(chunk_parts)

    return "# Retrieved Course Context\n\n" + "\n\n---\n\n".join(parts)


_STUDY_FILE_RE = re.compile(r"(syllabus|schedule|calendar|course[_\s-]*(?:info|information))", re.I)


def _clip_source(title: str, text: str, limit: int) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return f"## {title}\n{cleaned}"
    return f"## {title}\n{cleaned[:limit].rstrip()}\n\n[truncated]"


def build_tutor_context(course: str, query: str | None = None, max_chars: int = 62000) -> str:
    """Build bounded tutor context, retrieving relevant course chunks for query."""
    parts = []
    facts = _course_fact_snippets(course, max_chars=min(14000, max_chars // 3))
    if facts:
        parts.append(facts)
    remaining = max_chars - sum(len(p) for p in parts)
    relevant = _relevant_context(course, query, max_chars=max(12000, remaining))
    if relevant:
        parts.append(relevant)
    return "\n\n---\n\n".join(parts)


def build_study_file_context(course: str, query: str | None = None, max_chars: int = 115000) -> str:
    """Build broader context for tutor-generated study documents."""
    parts: list[str] = []

    def add(block: str) -> None:
        if block:
            parts.append(block)

    add(_course_fact_snippets(course, max_chars=18000))

    knowledge = store.get_course_knowledge(course)
    if knowledge and knowledge.get("content"):
        add(_clip_source("Canvas course information, syllabus, pages, announcements", knowledge["content"], 18000))

    for material in store.list_course_materials(course):
        name = material.get("file_name") or "Posted file"
        if _STUDY_FILE_RE.search(name):
            add(_clip_source(f"Posted course file: {name}", material.get("content") or "", 22000))

    sheet = store.get_cheat_sheet(course)
    if sheet and sheet.get("content"):
        add(_clip_source(f"Existing process and homework cheat sheet - {course}", sheet["content"], 36000))

    notes = store.get_course_notes(course)
    if notes and notes.get("content"):
        add(_clip_source(f"Class notes - {course}", notes["content"], 52000))

    used = 0
    kept: list[str] = []
    for block in parts:
        if used + len(block) > max_chars and kept:
            continue
        kept.append(block)
        used += len(block)
        if used >= max_chars:
            break

    remaining = max_chars - used
    if remaining > 12000:
        relevant = _relevant_context(course, query, max_chars=remaining)
        if relevant:
            kept.append(relevant)

    return "\n\n---\n\n".join(kept)
