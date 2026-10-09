"""Helpers for tutor-generated downloadable study files."""
from __future__ import annotations

import datetime as dt
import re

_FILE_TERMS_RE = re.compile(r"\b(file|download|document|doc|pdf|markdown|md)\b", re.I)
_DOC_KIND_RE = re.compile(
    r"\b("
    r"cheat\s*sheet|study\s*guide|review\s*sheet|review\s*guide|"
    r"exam\s*guide|midterm\s*guide|final\s*guide|practice\s*test|"
    r"outline|summary\s*sheet"
    r")\b",
    re.I,
)
_ACTION_RE = re.compile(r"\b(make|create|generate|write|build|save|export|put together)\b", re.I)
_EXAM_RE = re.compile(r"\b(midterm|final|exam|test|quiz)\b", re.I)
_PROGRAMMING_HELP_RE = re.compile(
    r"\b("
    r"programming|coding|code|debug|bug|traceback|stack\s*trace|error|"
    r"script|source\s*file|function|method|class|api|terminal|"
    r"python|java|javascript|typescript|html|css|react|streamlit|sql|"
    r"\.py|\.js|\.ts|\.java|\.html|\.css|\.ipynb"
    r")\b",
    re.I,
)


def is_programming_help(prompt: str) -> bool:
    """Return True for prompts that are asking for code/source-programming help."""
    return bool(_PROGRAMMING_HELP_RE.search(prompt or ""))


def wants_file(prompt: str) -> bool:
    """Return True when a tutor prompt should be saved as a downloadable file."""
    text = prompt or ""
    has_doc_kind = bool(_DOC_KIND_RE.search(text))
    if is_programming_help(text) and not has_doc_kind:
        return False
    if _FILE_TERMS_RE.search(text) and (_ACTION_RE.search(text) or has_doc_kind):
        return True
    if has_doc_kind and (_ACTION_RE.search(text) or _EXAM_RE.search(text)):
        return True
    return False


def title_from_prompt(course: str, prompt: str) -> str:
    """Create a stable human-readable title for a tutor-generated file."""
    text = (prompt or "").lower()
    if "midterm" in text and "cheat" in text:
        return f"{course} Midterm Cheat Sheet"
    if "midterm" in text:
        return f"{course} Midterm Study Guide"
    if "final" in text and "cheat" in text:
        return f"{course} Final Cheat Sheet"
    if "final" in text:
        return f"{course} Final Study Guide"
    if "practice test" in text:
        return f"{course} Practice Test"
    if "cheat" in text:
        return f"{course} Cheat Sheet"
    if "review" in text:
        return f"{course} Review Guide"
    if "outline" in text:
        return f"{course} Outline"
    return f"{course} Study Guide"


def fallback_title(course: str) -> str:
    """Title for a manually-saved response with no clear source prompt to name it after."""
    return f"{course} Saved Response — {dt.datetime.now():%b %d, %I:%M %p}"


def filename_for(title: str) -> str:
    """Return a safe PDF filename for a generated tutor document."""
    base = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return f"{base or 'tutor_file'}.pdf"


def markdown_document(title: str, content: str) -> str:
    """Ensure generated content is a standalone Markdown document."""
    body = (content or "").strip()
    if not body:
        return f"# {title}\n\n"
    if body.startswith("# "):
        return body + "\n"
    return f"# {title}\n\n{body}\n"
