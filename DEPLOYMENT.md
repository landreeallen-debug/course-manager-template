# Course Manager Deployment

## Start Command

```bash
streamlit run app.py --server.headless true --server.port $PORT
```

If the host does not provide `PORT`, use:

```bash
streamlit run app.py --server.headless true --server.port 8501
```

## Persistent Data

Set `COURSE_MANAGER_DATA_DIR` to a persistent disk directory. The app stores SQLite
databases, Canvas config, and uploaded textbook images there.

Recommended:

```bash
COURSE_MANAGER_DATA_DIR=/data/course-manager
```

Back up this directory regularly. The app also has an `Export backup` button in
the sidebar.

## Secrets

Set secrets as environment variables on the host:

```bash
ANTHROPIC_API_KEY=...
ACADEMIC_RECORDS_TEXT='...'
CANVAS_BASE_URL=https://yourschool.instructure.com
CANVAS_TOKEN=...
STUDENT_NAME=...
SCHOOL_NAME=...
```

`ACADEMIC_RECORDS_TEXT` is the private transcript/degree-requirements text used
by the advisor and graduation planner — optional if you only want Canvas
tracking and the tutor. Keep it in your host's environment variables instead
of committing `academic_records.txt` to GitHub.

`STUDENT_NAME` and `SCHOOL_NAME` are optional — used in the Home page greeting
and the advisor's persona. Both default to generic text if unset.

## Health Check

Use Streamlit's health endpoint:

```text
/_stcore/health
```

Expected response:

```text
ok
```

## Notes

- SQLite is acceptable for a private single-user deployment only if the host has
  persistent disk.
- Canvas sync is incremental for extracted course materials. Files are re-read
  only when Canvas `updated_at` changes.
- Tutor responses use retrieval, not the full course archive, so each question
  gets only relevant syllabus/files/notes chunks plus high-priority logistics.
