"""GPA math on the UNC-Chapel Hill scale.

UNC undergraduate grades carry a minus on A and a plus on D — so there is **no A+
and no D-**. Transfer/CR and pass-type grades don't count toward GPA, so only the
letter grades below are used. (Source: registrar.unc.edu / catalog.unc.edu.)
"""
from __future__ import annotations

GRADE_POINTS: dict[str, float] = {
    "A": 4.0, "A-": 3.7,
    "B+": 3.3, "B": 3.0, "B-": 2.7,
    "C+": 2.3, "C": 2.0, "C-": 1.7,
    "D+": 1.3, "D": 1.0,
    "F": 0.0,
}
GRADE_OPTIONS = list(GRADE_POINTS)


def gpa(entries: list[dict]) -> tuple[float | None, float]:
    """Weighted GPA over entries with a letter grade. Returns (gpa, credits)."""
    points = credits = 0.0
    for e in entries:
        g = e.get("grade")
        c = e.get("credits") or 0
        if g in GRADE_POINTS and c:
            points += GRADE_POINTS[g] * c
            credits += c
    return (round(points / credits, 3), credits) if credits else (None, 0.0)


def major_gpa(entries: list[dict], subject: str) -> tuple[float | None, float]:
    """GPA over only the entries whose course code starts with `subject` (e.g. 'COMP')."""
    subj = subject.upper()
    return gpa([e for e in entries if (e.get("course") or "").upper().startswith(subj)])


def closest_letter(points: float) -> str:
    """The letter grade nearest to a grade-point value (for what-if readouts)."""
    return min(GRADE_POINTS, key=lambda g: abs(GRADE_POINTS[g] - points))


def needed_average(
    current_gpa: float, current_credits: float, target_gpa: float, remaining_credits: float
) -> float | None:
    """Average grade-points needed over `remaining_credits` to reach `target_gpa` overall.

    May return >4.0 (impossible) or <0 (already there) — the caller interprets.
    """
    if remaining_credits <= 0:
        return None
    have = current_gpa * current_credits
    need_total = target_gpa * (current_credits + remaining_credits)
    return round((need_total - have) / remaining_credits, 3)


def final_grade_needed(graded_avg: float, final_weight_pct: float, target_pct: float) -> float | None:
    """Score needed on the remaining work to hit a target overall course percentage.

    graded_avg: your average (%) on work completed so far.
    final_weight_pct: percent of the grade still ungraded (e.g. the final exam).
    target_pct: desired overall course percentage.
    Returns the required score (%) — may be >100 (impossible) or <=0 (already secured).
    """
    if final_weight_pct <= 0:
        return None
    graded_weight = 100.0 - final_weight_pct
    earned = graded_avg * graded_weight / 100.0
    return round((target_pct - earned) / (final_weight_pct / 100.0), 1)
