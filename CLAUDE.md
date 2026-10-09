# Course Manager - Degree Planner (template)

This is a generic copy of a personal Streamlit degree-planner/Canvas-tracker app.
**Customize the placeholders below for your own school and majors before using it.**

## What This Does
Tracks Canvas assignments and course materials, generates study notes and a
tutor grounded in your posted files, and (optionally) helps plan future
semesters against your transcript and a course catalog.

## Setup checklist
1. Set `CANVAS_TOKEN` / `CANVAS_BASE_URL` to your own Canvas account (see
   `DEPLOYMENT.md`) — never share or commit your token.
2. Set `ANTHROPIC_API_KEY`.
3. Optionally set `STUDENT_NAME` and `SCHOOL_NAME` — used in the Home page
   greeting and the advisor's persona.
4. If you want the Planning page's advisor/graduation-plan/schedule-builder
   tools: set `ACADEMIC_RECORDS_TEXT` to your own transcript + degree
   requirements text, and replace `offerings/fall2026/*.json` with your own
   school's course catalog data (these files are UNC-Chapel Hill's Fall 2026
   catalog and won't mean anything at another school).
5. `planner.py`'s `PREREQS` map is UNC CS/Math prerequisite data — harmless
   no-op for other majors/schools, but feel free to replace it with your own.

## Files
- `app.py` — Streamlit navigation (Home / Classes / Planning).
- `pages/0_Home.py` — assignment dashboard.
- `pages/1_Classes.py` — per-course assignments, study notes, and an AI tutor.
- `pages/3_Planning.py` — course advisor, graduation plan, schedule builder.
- `canvas.py` — Canvas API client (personal access token).
- `agent.py` / `tools.py` — the advisor's system prompt and tools.
- `DEPLOYMENT.md` — environment variables and hosting notes.
