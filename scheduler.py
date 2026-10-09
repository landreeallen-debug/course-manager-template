"""Build conflict-free schedules from candidate courses.

Pure logic over the catalog: parse each section's meeting blocks, detect time
conflicts, and backtrack to find combinations that pick one section per course
with no overlaps. Lectures (LEC) are treated as the schedulable unit; a course's
labs/recitations are flagged for manual handling (their linkage to a specific
lecture isn't reliably present in the ConnectCarolina data).
"""
from __future__ import annotations

import re

import catalog

DAY_TOKENS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
DAY_NAMES = {
    "Mo": "Monday",
    "Tu": "Tuesday",
    "We": "Wednesday",
    "Th": "Thursday",
    "Fr": "Friday",
    "Sa": "Saturday",
    "Su": "Sunday",
}


# ── Parsing ──────────────────────────────────────────────────────────────────
def parse_days(code: str) -> set[str]:
    """'TuTh' -> {'Tu','Th'}; 'TBA'/'' -> set()."""
    if not code or code.upper() == "TBA":
        return set()
    out, i = set(), 0
    while i < len(code) - 1:
        tok = code[i : i + 2]
        if tok in DAY_TOKENS:
            out.add(tok)
        i += 2
    return out


def to_minutes(raw: str | None) -> int | None:
    """'13.45.00.000000' -> 825 (minutes since midnight)."""
    if not raw:
        return None
    p = raw.split(".")
    return int(p[0]) * 60 + int(p[1])


def fmt_minutes(m: int) -> str:
    h, mn = divmod(m, 60)
    ap = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{mn:02d} {ap}"


def _to_credits(units) -> float:
    try:
        return float(units)
    except (TypeError, ValueError):
        m = re.search(r"\d+(\.\d+)?", str(units or ""))
        return float(m.group()) if m else 0.0


def meetings(c: dict) -> list[tuple[str, int, int]]:
    """Timed meeting blocks as (day, start_min, end_min). Skips TBA/untimed."""
    blocks = []
    for m in c.get("meetings") or []:
        days = parse_days(m.get("days", ""))
        smin = to_minutes(m.get("start_time"))
        emin = to_minutes(m.get("end_time"))
        if not days or smin is None or emin is None or smin == emin:
            continue
        for d in days:
            blocks.append((d, smin, emin))
    return blocks


def _parse_course(s: str) -> tuple[str, str] | None:
    m = re.match(r"\s*([A-Za-z]{2,4})\s*([0-9]+[A-Za-z]?)\s*$", s or "")
    return (m.group(1).upper(), m.group(2).upper()) if m else None


def _section(c: dict) -> dict:
    proj = catalog._project(c)  # course, section, title, instructor, meets, seats_open, ...
    m0 = (c.get("meetings") or [{}])[0]
    return {
        **proj,
        "credits": _to_credits(c.get("units")),
        "blocks": meetings(c),
        "term_start": m0.get("start_dt"),  # MM/DD/YYYY
        "term_end": m0.get("end_dt"),
        "location": m0.get("facility_descr", ""),
    }


def sections_for(subject: str, nbr: str, open_only: bool = False) -> tuple[list[dict], dict]:
    """Schedulable lecture sections for one course, plus an info dict."""
    subject, nbr = subject.upper(), str(nbr).upper()
    raw = catalog._load_subject(subject)
    matches = [c for c in raw if str(c.get("catalog_nbr", "")).upper() == nbr]
    info = {
        "not_offered": len(matches) == 0,
        "has_lab_rec": any(c.get("component") in ("LAB", "REC") for c in matches),
        "untimed": False,
    }
    lec = [c for c in matches if c.get("component") == "LEC"]
    primary = lec or [c for c in matches if c.get("component") not in ("LAB", "REC")] or matches
    secs = []
    for c in primary:
        if open_only and (c.get("enrollment_available") or 0) <= 0:
            continue
        secs.append(_section(c))
    info["untimed"] = bool(secs) and all(not s["blocks"] for s in secs)
    return secs, info


# ── Conflict detection & search ──────────────────────────────────────────────
def conflict(a: list[tuple[str, int, int]], b: list[tuple[str, int, int]]) -> bool:
    """True if any blocks share a day with overlapping time."""
    for da, sa, ea in a:
        for db, sb, eb in b:
            if da == db and sa < eb and sb < ea:
                return True
    return False


def meeting_patterns(section: dict) -> list[dict]:
    """Group a section's blocks into recurring patterns: {days, start_min, end_min}."""
    groups: dict[tuple[int, int], set[str]] = {}
    for d, s, e in section["blocks"]:
        groups.setdefault((s, e), set()).add(d)
    return [{"days": days, "start_min": s, "end_min": e} for (s, e), days in groups.items()]


def build_schedules(
    courses: list[str],
    open_only: bool = False,
    max_results: int = 6,
    max_explore: int = 5000,
) -> dict:
    """Return conflict-free combinations (one section per course) + a report of
    courses that couldn't be placed."""
    reports = {"invalid": [], "not_offered": [], "no_sections": [], "lab_rec": []}
    candidates: list[list[dict]] = []

    for raw in courses:
        parsed = _parse_course(raw)
        if not parsed:
            reports["invalid"].append(raw)
            continue
        subj, nbr = parsed
        label = f"{subj} {nbr}"
        secs, info = sections_for(subj, nbr, open_only)
        if info["not_offered"]:
            reports["not_offered"].append(label)
            continue
        if info["has_lab_rec"]:
            reports["lab_rec"].append(label)
        if not secs:
            reports["no_sections"].append(label)  # e.g. open_only filtered them all out
            continue
        candidates.append(secs)

    results: list[list[dict]] = []
    explored = [0]

    def backtrack(i: int, chosen: list[dict]) -> None:
        if explored[0] > max_explore or len(results) >= max_results * 5:
            return
        if i == len(candidates):
            results.append(list(chosen))
            return
        for sec in candidates[i]:
            explored[0] += 1
            if all(not conflict(sec["blocks"], c["blocks"]) for c in chosen):
                chosen.append(sec)
                backtrack(i + 1, chosen)
                chosen.pop()

    backtrack(0, [])

    def score(combo: list[dict]):
        days = {d for s in combo for d, _, _ in s["blocks"]}
        all_open = all((s["seats_open"] or 0) > 0 for s in combo)
        return (not all_open, len(days))  # prefer all-open, then fewer days on campus

    results.sort(key=score)
    schedules = [
        {"sections": combo, "total_credits": round(sum(s["credits"] for s in combo), 1)}
        for combo in results[:max_results]
    ]
    return {"schedules": schedules, "reports": reports}
