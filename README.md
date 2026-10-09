# Course Manager

A personal Streamlit app that connects to Canvas to track assignments, generate
study notes and a per-course AI tutor, and (optionally) help plan future
semesters with an AI advisor, a graduation planner, and a conflict-free
schedule builder — powered by the Anthropic Claude API.

This is a generic copy meant to be customized for your own school, majors,
and Canvas account. See `CLAUDE.md` for a setup checklist.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file in this folder:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   CANVAS_TOKEN=...
   CANVAS_BASE_URL=https://yourschool.instructure.com
   ```
   (`.env` is gitignored — never commit your keys.)
3. Generate your own Canvas access token: Canvas → Account → Settings →
   **+ New Access Token**.

## Run

```bash
streamlit run app.py
```

## How it works

- **`app.py`** - Streamlit navigation and global page config.
- **`pages/0_Home.py`** - dashboard of upcoming/overdue assignments.
- **`pages/1_Classes.py`** - per-course assignments, auto-generated study notes
  and cheat sheets, and an AI tutor grounded in that course's posted files.
- **`pages/3_Planning.py`** - opens on a card grid for three optional tools:
  - **Course Assistant** - a chat advisor that reads your transcript via
    `ACADEMIC_RECORDS_TEXT` and searches a live course catalog.
  - **Graduation Plan** - a term-by-term plan toward your remaining
    requirements, with a deterministic prerequisite/credit-load check.
  - **Schedule Builder** - conflict-free schedules from the course catalog.
- **`canvas.py`** - Canvas import via a personal access token.
- **`assignments_store.py`** - assignment store with cross-source de-duplication.
- **`notes_engine.py`** / **`study_notes_store.py`** - study notes + tutor
  context, generated from Canvas files.
- **`agent.py`** / **`tools.py`** - the advisor's system prompt and tools.
- **`catalog.py`** / **`offerings/fall2026/*.json`** - course catalog search.
  The bundled `offerings/` data is UNC-Chapel Hill's Fall 2026 catalog;
  replace it with your own school's data (or ignore the Planning page
  entirely) if you're elsewhere.
- **`planner.py`** - graduation-plan validation + a starter UNC CS/Math
  prerequisite map. Harmless no-op for other majors; replace with your own.
- **`scheduler.py`** - meeting-time conflict detection + combinations.
- **`gpa.py`** / **`grades_store.py`** - GPA math and a logged-grades store.

## Degree planning (optional)

The Planning page's Course Assistant, Graduation Plan, and Schedule Builder
only make sense if you set `ACADEMIC_RECORDS_TEXT` (your transcript + degree
requirements, as plain text) and, if you want catalog search/scheduling,
your own school's course data in `offerings/`. Without those, just use Home
and Classes for Canvas tracking, study notes, and the tutor.

## Assignments (syllabus + Canvas)

The **Home** and **Classes** pages track your coursework from two sources,
de-duplicated:

- **Syllabus PDF** - the tutor extracts assignments, exams, due dates, and
  weights from a posted syllabus.
- **Canvas** - imported automatically with your personal access token.

When the same item comes from both, it's merged into one row rather than
listed twice (matched on course + similar title + close due date).
