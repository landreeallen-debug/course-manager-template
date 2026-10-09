"""Load and search UNC course offerings (ConnectCarolina JSON dumps).

The `offerings/<term>/` directory holds one JSON file per subject code
(PHIL.json, COMP.json, ...), each with a "classes" array of section records.
This module loads them lazily and projects each section down to the handful
of fields a student actually cares about.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TERM = "fall2026"
TERM_LABEL = "Fall 2026"
OFFERINGS_DIR = Path(__file__).parent / "offerings" / TERM


def available_subjects() -> list[str]:
    """All subject codes that have offerings this term, e.g. ['AAAD', ...]."""
    return sorted(p.stem for p in OFFERINGS_DIR.glob("*.json"))


@lru_cache(maxsize=None)
def _load_subject(subject: str) -> tuple[dict, ...]:
    path = OFFERINGS_DIR / f"{subject.upper()}.json"
    if not path.exists():
        return ()
    data = json.loads(path.read_text())
    return tuple(data.get("classes", []))


def _format_time(raw: str) -> str:
    # "12.30.00.000000" -> "12:30"
    if not raw:
        return ""
    parts = raw.split(".")
    return f"{int(parts[0]):02d}:{parts[1]}"


def _format_meetings(meetings: list[dict]) -> str:
    out = []
    for m in meetings or []:
        days = m.get("days") or "TBA"
        start = _format_time(m.get("start_time", ""))
        end = _format_time(m.get("end_time", ""))
        when = f"{days} {start}-{end}" if start else days
        where = m.get("facility_descr", "")
        out.append(f"{when} @ {where}" if where else when)
    return "; ".join(out) or "TBA"


def _project(c: dict) -> dict:
    """Trim a raw section record to a compact, model-friendly dict."""
    instructors = ", ".join(i.get("name", "") for i in c.get("instructors", [])) or "TBA"
    return {
        "course": f"{c.get('subject')} {c.get('catalog_nbr')}",
        "section": c.get("class_section"),
        "class_nbr": c.get("class_nbr"),
        "title": c.get("descr"),
        "units": c.get("units"),
        "component": c.get("section_type") or c.get("component"),
        "instructor": instructors,
        "meets": _format_meetings(c.get("meetings", [])),
        "status": c.get("enrl_stat_descr"),
        "seats_open": c.get("enrollment_available"),
        "attributes": c.get("crse_attr", ""),
        "session": c.get("session_descr"),
        "topic": c.get("topic") or "",
    }


def search(
    subject: str | None = None,
    catalog_nbr: str | None = None,
    keyword: str | None = None,
    days: str | None = None,
    open_only: bool = False,
    attribute: str | None = None,
    limit: int = 40,
) -> list[dict]:
    """Filter this term's sections. All criteria are optional and AND-ed together.

    - subject: subject code, e.g. "PHIL", "COMP"
    - catalog_nbr: course number prefix, e.g. "210" or "21"
    - keyword: case-insensitive substring of the title/topic
    - days: substring of the meeting day code, e.g. "TuTh", "MW"
    - open_only: only sections with seats available
    - attribute: a course attribute / gen-ed code present in crse_attr, e.g. "GE"
    """
    results: list[dict] = []
    subjects = [subject.upper()] if subject else available_subjects()
    kw = keyword.lower() if keyword else None
    attr = attribute.upper() if attribute else None
    num = str(catalog_nbr).lower() if catalog_nbr else None

    for subj in subjects:
        for c in _load_subject(subj):
            if num and not str(c.get("catalog_nbr", "")).lower().startswith(num):
                continue
            if kw and kw not in (c.get("descr", "") or "").lower() and kw not in (c.get("topic", "") or "").lower():
                continue
            if attr and attr not in (c.get("crse_attr", "") or "").upper():
                continue
            if days:
                meeting_days = " ".join(m.get("days", "") for m in c.get("meetings", []))
                if days.lower() not in meeting_days.lower():
                    continue
            if open_only and (c.get("enrollment_available") or 0) <= 0:
                continue
            results.append(_project(c))
            if len(results) >= limit:
                return results
    return results
