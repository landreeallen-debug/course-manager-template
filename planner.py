"""Graduation planner support: prerequisites + validation.

The term-by-term plan itself is produced by the advisor (`agent.graduation_plan`);
this module supplies the deterministic checks — an accurate prerequisite map and a
validator that flags ordering/credit-load problems.

PREREQS is sourced from the official UNC catalog (catalog.unc.edu), fetched
2026-06-08. Each course maps to:
    "prereq": list of groups that must be satisfied BEFORE the term (any one code
              in a group satisfies that group)
    "coreq":  list of groups that may be satisfied in the SAME term or earlier
Only the quantitative core that actually gates sequencing is encoded; Philosophy
electives and gen-ed have no hard prerequisites and are left to the advisor.
"""
from __future__ import annotations

import re

import config

# Sourced verbatim from catalog.unc.edu (COMP/MATH/STOR), 2026-06-08.
PREREQS: dict[str, dict[str, list[list[str]]]] = {
    "COMP 110": {
        "prereq": [["MATH 130", "MATH 152", "MATH 210", "MATH 231", "MATH 129P",
                    "PHIL 155", "STOR 120", "STOR 151", "STOR 155"]],
        "coreq": [],
    },
    "COMP 210": {"prereq": [["COMP 110"], ["MATH 231"]],
                 "coreq": [["COMP 283", "MATH 381", "STOR 315"]]},
    "COMP 283": {"prereq": [["MATH 231", "MATH 241"]], "coreq": []},
    "COMP 211": {"prereq": [["COMP 210"], ["COMP 283", "MATH 381", "STOR 315"]], "coreq": []},
    "COMP 301": {"prereq": [["COMP 210"], ["COMP 283", "MATH 381", "STOR 315"]], "coreq": []},
    "COMP 311": {"prereq": [["COMP 211"]], "coreq": []},
    "MATH 231": {"prereq": [["MATH 110"], ["MATH 130"]], "coreq": []},
    "MATH 232": {"prereq": [["MATH 231"]], "coreq": []},
    "MATH 233": {"prereq": [["MATH 232"]], "coreq": []},
    "MATH 381": {"prereq": [["MATH 232", "MATH 231", "MATH 210"]], "coreq": []},
    "STOR 155": {"prereq": [["MATH 110"]], "coreq": []},
    "STOR 315": {"prereq": [["MATH 232"]], "coreq": []},
    "STOR 215": {"prereq": [["MATH 110"]], "coreq": []},
}

# Core courses requiring a grade of C (or C-) or better — surfaced as a reminder.
GRADE_C_NOTE = (
    "Reminder: the CS/MATH core (COMP 110/210/211/283/301/311, MATH 231/232) requires "
    "a grade of C or better — confirm exact grade minimums in the UNC catalog."
)

_CODE_RE = re.compile(r"\b([A-Z]{2,4})\s+(\d{2,3}[A-Z]?)\b")


def _norm(code: str) -> str:
    return re.sub(r"\s+", " ", code or "").strip().upper()


def completed_courses() -> set[str]:
    """Concrete course codes the student has completed or has in progress.

    Only scans the transcript region (transfer + test + UNC coursework), i.e. the
    text *before* the degree-progress/requirements sections, so required-but-untaken
    courses listed later aren't mistaken for completed ones.
    """
    text = config.academic_records_text()
    transcript = text.split("--- DEGREE PROGRESS SUMMARY ---", 1)[0]
    return {f"{m.group(1)} {m.group(2)}" for m in _CODE_RE.finditer(transcript)}


def _group_met(group: list[str], have: set[str]) -> bool:
    return any(_norm(alt) in have for alt in group)


def validate(plan: dict) -> list[str]:
    """Return human-readable warnings about prereq ordering and credit loads.

    Co-requisites may be satisfied in the same term; prerequisites must appear in an
    earlier term or already be completed.
    """
    warnings: list[str] = []
    have = {_norm(c) for c in completed_courses()}
    needs_grade_note = False

    for term in plan.get("terms", []):
        label = term.get("term", "?")
        courses = term.get("courses", [])
        term_codes = {_norm(c.get("code", "")) for c in courses}
        planned = [c for c in courses if c.get("status", "planned") == "planned"]

        # Only check prerequisites for courses we're actually planning (not history).
        for course in planned:
            code = _norm(course.get("code", ""))
            rule = PREREQS.get(code)
            if code in PREREQS:
                needs_grade_note = needs_grade_note or code in {
                    "COMP 110", "COMP 210", "COMP 211", "COMP 283", "COMP 301", "COMP 311",
                    "MATH 231", "MATH 232",
                }
            if not rule:
                continue
            for grp in rule["prereq"]:
                if not _group_met(grp, have):
                    warnings.append(
                        f"Warning: {label}: {code} needs {' or '.join(grp)} first - not completed "
                        "or scheduled earlier."
                    )
            for grp in rule["coreq"]:
                if not _group_met(grp, have | term_codes):
                    warnings.append(
                        f"Warning: {label}: {code} needs {' or '.join(grp)} before or alongside it."
                    )

        # Credit-load flags apply only to terms we're planning, not historical ones.
        if planned:
            credits = term.get("credits") or sum(c.get("credits", 0) for c in courses)
            if credits and credits > 18:
                warnings.append(f"Warning: {label}: {credits} credits is a heavy load (>18).")
            elif credits and credits < 12 and "summer" not in label.lower():
                warnings.append(f"ℹ️ {label}: {credits} credits is below full-time (12).")

        have |= term_codes

    if needs_grade_note:
        warnings.append(GRADE_C_NOTE)
    return warnings


def build(
    start_term: str = "Spring 2027",
    credits_per_term: int = 15,
    include_summer: bool = False,
    graduate_by: str | None = None,
) -> dict:
    """Generate a plan via the advisor and attach validation warnings."""
    import agent  # local import to avoid a heavy import at module load

    plan = agent.graduation_plan(
        start_term=start_term,
        credits_per_term=credits_per_term,
        include_summer=include_summer,
        graduate_by=graduate_by,
    )
    return {"plan": plan, "warnings": validate(plan)}
