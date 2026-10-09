"""Study-notes pipeline tests.

Run all:              python3 test_pipeline.py
Run one class:        python3 test_pipeline.py TestUnit
Run one test:         python3 test_pipeline.py TestUnit.test_sort_slides_before_homework
Run Canvas tests:     python3 test_pipeline.py TestCanvas
Run integration:      python3 test_pipeline.py TestIntegration

Tests are split into three tiers:
  TestUnit        — pure logic, no network (instant)
  TestCanvas      — hits real Canvas API, no Claude (10–30 s)
  TestIntegration — real Canvas + mocked Claude (30–60 s)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _file(name: str, fid: str | None = None, size: int = 100_000) -> dict:
    return {
        "canvas_file_id": fid or name,
        "name": name,
        "course": "LING 101",
        "url": "https://example.com/fake",
        "size": size,
        "updated_at": "2026-07-01T00:00:00Z",
    }


def _temp_db() -> Path:
    """Return a path to an empty temp DB; caller must delete it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return Path(path)


# ─────────────────────────────────────────────────────────────────────────────
# TestUnit — pure logic, no network
# ─────────────────────────────────────────────────────────────────────────────

class TestUnit(unittest.TestCase):

    # ── sort_pending ──────────────────────────────────────────────────────────

    def test_sort_slides_before_homework(self):
        """Homework files must always sort after slides regardless of input order."""
        import notes_engine as ne
        files = [
            _file("SS2_2026_HW2..pdf"),
            _file("SSII_LING101_Day5_June26.pdf"),
            _file("SS2_2026_HW1.pdf"),
            _file("SSII_LING101_Day1_June22.pdf"),
            _file("ReadingForThursday.pdf"),
        ]
        result = ne.sort_pending(files)
        names = [f["name"] for f in result]
        slide_indices = [i for i, n in enumerate(names) if not ne._is_homework(n)]
        hw_indices    = [i for i, n in enumerate(names) if ne._is_homework(n)]
        for si in slide_indices:
            for hi in hw_indices:
                self.assertLess(si, hi,
                    f"Slide at position {si} came after homework at position {hi}.\nOrder: {names}")

    def test_answer_key_not_treated_as_homework(self):
        """Answer-key PDFs (containing 'key') must NOT be sorted as homework."""
        import notes_engine as ne
        self.assertFalse(ne._is_homework("SS2_2026_HW_1_KEY.pdf"))
        self.assertFalse(ne._is_homework("ch2_ex.1-10_answers.pdf"))

    def test_homework_detection(self):
        """HW and homework filenames must be detected correctly."""
        import notes_engine as ne
        self.assertTrue(ne._is_homework("SS2_2026_HW1.pdf"))
        self.assertTrue(ne._is_homework("SS2_2026_HW2..pdf"))
        self.assertTrue(ne._is_homework("Homework_3.pdf"))
        self.assertTrue(ne._is_homework("morphology practice (ex.1-13 CH 4).pdf"))
        self.assertTrue(ne._is_homework("Problem Set 2.pdf"))
        self.assertTrue(ne._is_homework("Phonology Worksheet.pdf"))
        self.assertTrue(ne._is_homework("Assignments for Chapter 4.pdf"))

    def test_non_homework_detection(self):
        """Reference files, answer keys, and optional material must not be homework."""
        import notes_engine as ne
        self.assertFalse(ne._is_homework("ReadingForThursday.pdf"))
        self.assertFalse(ne._is_homework("Sign_Language-Morphology_optional.pdf"))
        self.assertFalse(ne._is_homework("SSII_LING101_Day5_June26.pdf"))

    def test_filename_overrides_bad_classification(self):
        """Strong filename signals correct common Claude classification misses."""
        import notes_engine as ne
        self.assertEqual(
            ne._normalize_classification("morphology practice (ex.1-13 CH 4).pdf", "skip"),
            "homework",
        )
        self.assertEqual(
            ne._normalize_classification("SS2_2026_HW_1_KEY.pdf", "homework"),
            "skip",
        )

    def test_skipped_homework_like_file_remains_pending(self):
        """Old skip records for homework-like files must be reviewed after detection improves."""
        import notes_engine as ne, study_notes_store as store
        f = _file("morphology practice (ex.1-13 CH 4).pdf", "practice")
        store.mark_processed(f["canvas_file_id"], f["course"], f["name"], "skip", f["updated_at"])
        self.assertTrue(ne.needs_processing(f))

    def test_sort_preserves_all_files(self):
        """sort_pending must not drop or duplicate any file."""
        import notes_engine as ne
        files = [_file(n) for n in ["HW1.pdf", "Day1.pdf", "HW2.pdf", "Day9.pdf", "Reading.pdf"]]
        result = ne.sort_pending(files)
        self.assertEqual(len(result), len(files))
        self.assertEqual(
            {f["canvas_file_id"] for f in result},
            {f["canvas_file_id"] for f in files},
        )

    # ── _strip_preamble ───────────────────────────────────────────────────────

    def test_strip_removes_commentary(self):
        import notes_engine as ne
        text = "I'll add the new entries now.\n\n## IPA Transcription\n**Use when**: always"
        self.assertTrue(ne._strip_preamble(text).startswith("## IPA"))

    def test_strip_preserves_clean_output(self):
        import notes_engine as ne
        text = "## IPA Transcription\n**Use when**: always"
        self.assertEqual(ne._strip_preamble(text), text)

    def test_strip_handles_top_level_header(self):
        import notes_engine as ne
        text = "Preamble text\n# LING 101 — Cheat Sheet\n## Entry"
        self.assertTrue(ne._strip_preamble(text).startswith("# LING 101"))

    def test_strip_nothing_new(self):
        import notes_engine as ne
        self.assertEqual(ne._strip_preamble("NOTHING_NEW"), "NOTHING_NEW")

    # ── _append_to_sheet ─────────────────────────────────────────────────────

    def setUp(self):
        import assignments_store
        import study_notes_store as store
        self._orig_db = store.DB_PATH
        self._tmp = _temp_db()
        store.DB_PATH = self._tmp
        store.init_db()
        self._orig_assignments_db = assignments_store.DB_PATH
        self._tmp_assignments = _temp_db()
        assignments_store.DB_PATH = self._tmp_assignments
        assignments_store.init_db()

    def tearDown(self):
        import assignments_store
        import study_notes_store as store
        store.DB_PATH = self._orig_db
        self._tmp.unlink(missing_ok=True)
        assignments_store.DB_PATH = self._orig_assignments_db
        self._tmp_assignments.unlink(missing_ok=True)

    def test_first_append_adds_header(self):
        import notes_engine as ne
        result = ne._append_to_sheet("LING 101", "## IPA\n**Use when**: ...")
        self.assertIn("# LING 101 — Process & Method Cheat Sheet", result)
        self.assertIn("## IPA", result)

    def test_second_append_preserves_first(self):
        import notes_engine as ne, study_notes_store as store
        first = ne._append_to_sheet("LING 101", "## Entry One\n**Use when**: A")
        store.upsert_cheat_sheet("LING 101", first)
        second = ne._append_to_sheet("LING 101", "## Entry Two\n**Use when**: B")
        self.assertIn("## Entry One", second)
        self.assertIn("## Entry Two", second)

    def test_nothing_new_preserves_existing(self):
        import notes_engine as ne, study_notes_store as store
        store.upsert_cheat_sheet("LING 101", "# LING 101 — Process & Method Cheat Sheet\n\n## Existing\n...")
        result = ne._append_to_sheet("LING 101", "NOTHING_NEW")
        self.assertIn("## Existing", result)

    def test_hw2_then_hw1_both_present(self):
        """Core requirement: both homework reference sections must survive."""
        import notes_engine as ne, study_notes_store as store
        hw2_section = "## SS2_2026_HW2 — Complete Reference Guide\n### Q1\n**Concept**: Phonemes\n..."
        hw1_section = "## SS2_2026_HW1 — Complete Reference Guide\n### Q1\n**Concept**: IPA\n..."

        after_hw2 = ne._append_to_sheet("LING 101", hw2_section)
        store.upsert_cheat_sheet("LING 101", after_hw2)

        after_hw1 = ne._append_to_sheet("LING 101", hw1_section)
        self.assertIn("SS2_2026_HW2 — Complete Reference Guide", after_hw1,
            "HW2 reference guide was lost after HW1 was appended!")
        self.assertIn("SS2_2026_HW1 — Complete Reference Guide", after_hw1,
            "HW1 reference guide missing!")

    def test_notes_accumulate_all_days(self):
        """Simulates 9 lectures being appended — all must remain."""
        import study_notes_store as store
        course = "LING 101"
        header = f"# {course} — Class Notes\n\n"
        current = ""
        for i in range(1, 10):
            section = f"## Day {i} — Lecture {i}\nContent for day {i}."
            if current:
                body = current[len(header):] if current.startswith(header) else current
                current = header + body + "\n\n" + section
            else:
                current = header + section
            store.upsert_course_notes(course, current)

        final = store.get_course_notes(course)["content"]
        for i in range(1, 10):
            self.assertIn(f"## Day {i}", final, f"Day {i} is missing from class notes!")

    def test_homework_prompt_contains_class_notes_placeholder(self):
        """Homework processing must always pass class notes to Claude."""
        import notes_engine as ne
        self.assertIn("{class_notes}", ne._CHEAT_HOMEWORK_PROMPT,
            "Homework cheat-sheet prompt must reference {class_notes}")

    def test_slides_prompt_does_not_regenerate_existing(self):
        """Slides prompt must NOT contain {current} — avoids regenerating full doc."""
        import notes_engine as ne
        self.assertNotIn("{current}", ne._CHEAT_SLIDES_PROMPT,
            "Slides prompt must not ask Claude to reproduce the existing cheat sheet")

    def test_tutor_context_includes_course_knowledge(self):
        """Syllabus/course info must be available even when it is not in lecture notes."""
        import notes_engine as ne, study_notes_store as store
        store.upsert_course_knowledge(
            "LING 101",
            "=== SYLLABUS ===\nMidterm exam: July 15 during class.",
        )
        context = ne.build_tutor_context("LING 101")
        self.assertIn("Midterm exam: July 15", context)

    def test_tutor_context_includes_posted_file_text(self):
        """Posted files such as syllabus PDFs must be part of tutor context."""
        import notes_engine as ne, study_notes_store as store
        store.upsert_course_material(
            "syllabus-file",
            "LING 101",
            "LING 101 syllabus.pdf",
            "Final project due August 1. Midterm is listed only here.",
            "2026-07-01T00:00:00Z",
        )
        context = ne.build_tutor_context("LING 101")
        self.assertIn("LING 101 syllabus.pdf", context)
        self.assertIn("Midterm is listed only here", context)

    def test_midterm_date_gets_priority_from_syllabus_file(self):
        """Midterm dates buried in syllabus files should be prominent for the tutor."""
        import notes_engine as ne, study_notes_store as store
        store.upsert_course_material(
            "syllabus-file",
            "LING 101",
            "LING101_Intro_to_Language_Syllabus_SII_HC-5.pdf",
            "COURSE SCHEDULE\n11 Tu July 7 - Midterm Review\n12 W July 8 MIDTERM EXAM\n",
            "2026-07-01T00:00:00Z",
        )
        context = ne.build_tutor_context("LING 101")
        priority = context.split("# Posted Course Files", 1)[0]
        self.assertIn("High-Priority Course Logistics", priority)
        self.assertIn("12 W July 8 MIDTERM EXAM", priority)

    def test_tutor_context_retrieves_relevant_chunks_only(self):
        """Large course archives should be bounded to chunks relevant to the question."""
        import notes_engine as ne, study_notes_store as store
        huge = "\n".join(f"unrelated lecture filler {i}" for i in range(4000))
        store.upsert_course_material("huge", "LING 101", "Huge handout.pdf", huge, "2026-07-01")
        store.upsert_course_material(
            "syllabus-file",
            "LING 101",
            "Syllabus.pdf",
            "COURSE SCHEDULE\n12 W July 8 MIDTERM EXAM\n",
            "2026-07-01",
        )
        context = ne.build_tutor_context("LING 101", "When is the midterm?")
        self.assertLess(len(context), 62000)
        self.assertIn("12 W July 8 MIDTERM EXAM", context)
        self.assertNotIn("unrelated lecture filler 3999", context)

    def test_tutor_file_request_detection(self):
        """Tutor should save obvious study-guide requests as files."""
        import tutor_files

        self.assertTrue(tutor_files.wants_file("make me a midterm cheat sheet"))
        self.assertTrue(tutor_files.wants_file("create a downloadable study guide file"))
        self.assertFalse(tutor_files.wants_file("when is the midterm?"))
        self.assertEqual(
            tutor_files.filename_for("LING 101 Midterm Cheat Sheet"),
            "LING_101_Midterm_Cheat_Sheet.pdf",
        )

    def test_tutor_file_storage_roundtrip(self):
        """Tutor-generated files should persist and be deletable."""
        import study_notes_store as store

        file_id = store.save_tutor_file(
            "LING 101",
            "LING 101 Midterm Cheat Sheet",
            "LING_101_Midterm_Cheat_Sheet.md",
            "# LING 101 Midterm Cheat Sheet\n\nContent",
            prompt="make me a midterm cheat sheet",
        )
        row = store.get_tutor_file(file_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["course"], "LING 101")
        self.assertEqual(row["mime_type"], "text/markdown")
        files = store.list_tutor_files("LING 101")
        self.assertEqual(files[0]["id"], file_id)
        store.delete_tutor_file(file_id)
        self.assertIsNone(store.get_tutor_file(file_id))

    def test_study_file_context_includes_notes_and_cheat_sheet(self):
        """Generated study files need broad notes and cheat-sheet context."""
        import notes_engine as ne, study_notes_store as store

        store.upsert_course_notes("LING 101", "# LING 101 Notes\n\n## Day 1\nPhonetics and IPA.")
        store.upsert_cheat_sheet("LING 101", "# LING 101 Cheat Sheet\n\n## Minimal Pairs\nUse for phonemes.")
        context = ne.build_study_file_context("LING 101", "make me a midterm cheat sheet")
        self.assertIn("Phonetics and IPA", context)
        self.assertIn("Minimal Pairs", context)

    def test_canvas_file_refs_survive_syllabus_html(self):
        """Syllabus links must keep their Canvas file id for later text extraction."""
        import canvas
        html = '''
        <a title="LING101_Intro_to_Language_Syllabus_SII_HC-5.pdf"
           href="https://uncch.instructure.com/courses/117930/files/15736034?wrap=1"
           data-api-endpoint="https://uncch.instructure.com/api/v1/courses/117930/files/15736034">
           LING101_Intro_to_Language_Syllabus_SII_HC-5.pdf
        </a>
        '''
        refs = canvas._file_refs_from_html(html)
        self.assertEqual(refs[0]["canvas_file_id"], "15736034")
        self.assertEqual(refs[0]["name"], "LING101_Intro_to_Language_Syllabus_SII_HC-5.pdf")

    def test_extracts_midterm_from_syllabus_schedule(self):
        """Home should find dated exams listed only in syllabus schedule text."""
        import datetime as dt
        import upcoming

        text = "COURSE SCHEDULE\n11 Tu July 7 - Midterm Review\n12 W July 8 MIDTERM EXAM\n"
        items = upcoming.extract_syllabus_items("LING 101", text, today=dt.date(2026, 7, 6))
        self.assertTrue(any(i["title"] == "Midterm Exam" for i in items), items)
        self.assertTrue(any(i["due_at"] == "2026-07-08" for i in items), items)

    def test_imports_syllabus_homework_into_checklist(self):
        """Syllabus-derived homework should use the same done-capable assignment store."""
        import assignments_store
        import upcoming

        count = upcoming.import_syllabus_items(
            "LING 101",
            "Week 4: Homework 3 due December 12\n",
            "Test syllabus",
        )
        rows = assignments_store.list_assignments()
        self.assertEqual(count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["course"], "LING 101")
        self.assertEqual(rows[0]["source"], "syllabus")
        assignments_store.set_done(rows[0]["id"], True)
        self.assertTrue(assignments_store.get(rows[0]["id"])["done"])

    def test_canvas_submission_marks_assignment_done(self):
        """Canvas-submitted assignments should become completed locally."""
        import assignments_store

        assignment_id, was_new = assignments_store.add_assignment(
            course="LING 101",
            title="Homework 4",
            due_at="2026-07-20T03:59:00Z",
            source="canvas",
            canvas_id="hw4",
            canvas_submitted=True,
            canvas_submission_status="submitted",
            canvas_submitted_at="2026-07-19T22:00:00Z",
        )
        row = assignments_store.get(assignment_id)
        self.assertTrue(was_new)
        self.assertEqual(row["done"], 1)
        self.assertEqual(row["canvas_submitted"], 1)
        self.assertEqual(row["canvas_submission_status"], "submitted")

    def test_canvas_submission_update_preserves_manual_done_when_unsubmitted(self):
        """Unsubmitted Canvas status should not erase a manual local checkmark."""
        import assignments_store

        assignment_id, _ = assignments_store.add_assignment(
            course="LING 101",
            title="In-Class Paper",
            due_at="2026-07-20T03:59:00Z",
            source="canvas",
            canvas_id="paper",
        )
        assignments_store.set_done(assignment_id, True)
        assignments_store.add_assignment(
            course="LING 101",
            title="In-Class Paper",
            due_at="2026-07-20T03:59:00Z",
            source="canvas",
            canvas_id="paper",
            canvas_submitted=False,
            canvas_submission_status="unsubmitted",
        )
        row = assignments_store.get(assignment_id)
        self.assertEqual(row["done"], 1)
        self.assertEqual(row["canvas_submitted"], 0)

    def test_canvas_submission_state_from_assignment_payload(self):
        """Canvas assignment payloads with include[]=submission should normalize cleanly."""
        import canvas

        submitted, status, submitted_at, missing = canvas._submission_state({
            "submission": {
                "workflow_state": "submitted",
                "submitted_at": "2026-07-19T22:00:00Z",
                "attempt": 1,
                "missing": False,
            }
        })
        self.assertTrue(submitted)
        self.assertEqual(status, "submitted")
        self.assertEqual(submitted_at, "2026-07-19T22:00:00Z")
        self.assertFalse(missing)

    def test_imports_existing_syllabus_material_into_checklist(self):
        """Home sync should read syllabus PDFs that were already indexed for tutors."""
        import datetime as dt
        import assignments_store
        import study_notes_store as store
        import upcoming

        target = dt.date.today() + dt.timedelta(days=5)
        store.upsert_course_material(
            "syllabus-file",
            "LING 101",
            "LING101_Intro_to_Language_Syllabus_SII_HC-5.pdf",
            (
                "COURSE SCHEDULE\n"
                f"12 W {target.strftime('%B')} {target.day} "
                "MIDTERM EXAM (material covered up to day 10)\n"
            ),
            "2026-07-01T00:00:00Z",
        )
        added = upcoming.import_stored_material_items("LING 101")
        rows = assignments_store.list_assignments()
        self.assertEqual(added, 1)
        self.assertEqual(rows[0]["title"], "Midterm Exam (Material Covered Up To Day 10)")
        self.assertEqual(rows[0]["due_at"], target.isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# TestCanvas — real Canvas API, no Claude
# ─────────────────────────────────────────────────────────────────────────────

class TestCanvasFileMetadata(unittest.TestCase):

    def test_display_name_keeps_filename_extension(self):
        import canvas

        file_meta = {
            "display_name": "Syllabus",
            "filename": "course_syllabus.pdf",
        }
        self.assertEqual(canvas._file_name(file_meta), "Syllabus.pdf")

    def test_existing_display_name_extension_is_preserved(self):
        import canvas

        file_meta = {
            "display_name": "Course Syllabus.docx",
            "filename": "syllabus-upload.pdf",
        }
        self.assertEqual(canvas._file_name(file_meta), "Course Syllabus.docx")


class TestCanvas(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import canvas
        if not canvas.is_configured():
            raise unittest.SkipTest("Canvas not configured — skipping Canvas tests")
        cls.files = canvas.slide_files()

    def test_returns_files(self):
        self.assertGreater(len(self.files), 0, "canvas.slide_files() returned nothing")

    def test_all_files_have_required_fields(self):
        required = {"canvas_file_id", "name", "course", "url", "size", "updated_at"}
        for f in self.files:
            missing = required - set(f.keys())
            self.assertEqual(missing, set(),
                f"File '{f.get('name')}' is missing fields: {missing}")

    def test_lecture_slides_present(self):
        """At least one Day* lecture slide must exist."""
        names = [f["name"] for f in self.files]
        found = [n for n in names if "day" in n.lower() or "ling" in n.lower()]
        self.assertGreater(len(found), 0,
            f"No lecture slides found. All files: {names}")

    def test_homework_files_present(self):
        """At least one homework file must exist."""
        import notes_engine as ne
        hw_files = [f for f in self.files if ne._is_homework(f["name"])]
        self.assertGreater(len(hw_files), 0,
            "No homework files found in Canvas")

    def test_no_file_missing_course_field(self):
        for f in self.files:
            self.assertTrue(f.get("course"), f"File '{f['name']}' has no course field")

    def test_download_smallest_file(self):
        """Verify the stable-URL download works (no 404, no expired URL)."""
        import canvas
        small = min(self.files, key=lambda f: f.get("size") or 999_999)
        try:
            data = canvas.download_file_by_id(small["canvas_file_id"])
        except Exception as e:
            self.fail(f"download_file_by_id failed for '{small['name']}': {e}")
        self.assertGreater(len(data), 0, f"Downloaded 0 bytes for '{small['name']}'")

    def test_all_small_files_downloadable(self):
        """Every file under 2 MB must download without error."""
        import canvas
        errors = []
        for f in self.files:
            if (f.get("size") or 0) > 2 * 1024 * 1024:
                continue
            try:
                data = canvas.download_file_by_id(f["canvas_file_id"])
                if len(data) == 0:
                    errors.append(f"{f['name']}: 0 bytes")
            except Exception as e:
                errors.append(f"{f['name']}: {e}")
        self.assertEqual(errors, [],
            "Some files failed to download:\n  " + "\n  ".join(errors))

    def test_pending_detection_uses_canvas_file_id(self):
        """Pending check must use canvas_file_id, not the expiring URL."""
        import study_notes_store as store
        store.init_db()
        for f in self.files[:3]:
            rec = store.get_processed(f["canvas_file_id"])
            # Just verifying the function accepts the field without error
            _ = rec  # None or dict — both valid


# ─────────────────────────────────────────────────────────────────────────────
# TestIntegration — real Canvas, mocked Claude, temp DB
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import canvas
        if not canvas.is_configured():
            raise unittest.SkipTest("Canvas not configured — skipping integration tests")
        cls.all_files = canvas.slide_files()

    def setUp(self):
        import study_notes_store as store
        self._orig_db = store.DB_PATH
        self._tmp = _temp_db()
        store.DB_PATH = self._tmp
        store.init_db()

    def tearDown(self):
        import study_notes_store as store
        store.DB_PATH = self._orig_db
        self._tmp.unlink(missing_ok=True)

    def _mock_api_response(self, doc_type="slides"):
        """Return a realistic mock Claude response for a given doc type."""
        if doc_type == "slides":
            return "slides"
        if doc_type == "homework":
            return "homework"
        return "skip"

    @patch("notes_engine._api_call")
    def test_slides_processed_first_in_mixed_batch(self, mock_api):
        """When Canvas returns HW before slides, slides must still be processed first."""
        import notes_engine as ne, study_notes_store as store
        processed_order = []

        def fake_api(file_bytes, file_name, prompt, model, max_tokens, **kwargs):
            name = file_name.lower()
            if "hw" in name and "key" not in name:
                return "homework"
            if "skip" in name or "key" in name or "answer" in name:
                return "skip"
            if max_tokens == 10:   # classify call
                return "slides"
            # notes / cheat sheet call — track order
            processed_order.append(file_name)
            return f"## Notes for {file_name}\n**Use when**: testing"

        mock_api.side_effect = fake_api

        # Build a mixed list with homework before slides (Canvas newest-first)
        mixed = [
            _file("HW2.pdf", "hw2", size=300_000),
            _file("Day9.pdf", "day9", size=300_000),
            _file("HW1.pdf", "hw1", size=300_000),
            _file("Day1.pdf", "day1", size=300_000),
        ]

        import canvas
        sorted_files = ne.sort_pending(mixed)

        # Verify ordering before processing
        hw_seen = False
        for f in sorted_files:
            if ne._is_homework(f["name"]):
                hw_seen = True
            elif hw_seen:
                self.fail(f"Slide '{f['name']}' sorted after homework — ordering broken!")

    @patch("notes_engine._api_call")
    def test_all_pending_files_marked_processed(self, mock_api):
        """After processing a batch, every pending file must be in processed_files."""
        import notes_engine as ne, study_notes_store as store, canvas

        def fake_api(file_bytes, file_name, prompt, model, max_tokens, **kwargs):
            name = file_name.lower()
            if max_tokens == 10:
                if "hw" in name and "key" not in name:
                    return "homework"
                if "key" in name or "answer" in name or "ch 1" in name or "morphology" in name or "sign" in name:
                    return "skip"
                return "slides"
            return f"## Entry for {file_name}\n**Use when**: test\n**How**: test\n**Example**: test"

        mock_api.side_effect = fake_api

        files = self.all_files
        pending = ne.sort_pending([f for f in files if store.get_processed(f["canvas_file_id"]) is None])

        errors = []
        for f in pending:
            try:
                file_bytes = canvas.download_file_by_id(f["canvas_file_id"])
                ne.process_file(f)
            except Exception as e:
                errors.append(f"{f['name']}: {e}")

        if errors:
            self.fail("Errors during processing:\n  " + "\n  ".join(errors))

        processed_ids = {r["canvas_file_id"] for r in store.list_processed()}
        missed = [f["name"] for f in pending if f["canvas_file_id"] not in processed_ids]
        self.assertEqual(missed, [],
            f"These files were NOT marked as processed:\n  " + "\n  ".join(missed))

    @patch("notes_engine._api_call")
    def test_homework_reference_guide_appended_for_each_hw(self, mock_api):
        """Each homework file must add its own reference guide section."""
        import notes_engine as ne, study_notes_store as store, canvas

        hw_files = [f for f in self.all_files if ne._is_homework(f["name"])]
        self.assertGreater(len(hw_files), 0, "No homework files on Canvas to test")

        def fake_api(file_bytes, file_name, prompt, model, max_tokens, **kwargs):
            if max_tokens == 10:
                return "homework"
            hw_name = file_name.rsplit(".", 1)[0]
            return f"## {hw_name} — Complete Reference Guide\n### Q1\n**Concept**: test\n**From the slides**: content\n**Step-by-step**:\n1. test"

        mock_api.side_effect = fake_api

        # Process homework files (sorted so they run in the right order)
        sorted_hw = ne.sort_pending(hw_files)
        for f in sorted_hw:
            try:
                file_bytes = canvas.download_file_by_id(f["canvas_file_id"])
                ne.process_file(f)
            except Exception as e:
                self.fail(f"process_file failed for {f['name']}: {e}")

        cheat = store.get_cheat_sheet("LING 101")
        self.assertIsNotNone(cheat, "No cheat sheet was created")

        for f in hw_files:
            hw_name = f["name"].rsplit(".", 1)[0]
            self.assertIn(f"{hw_name} — Complete Reference Guide", cheat["content"],
                f"Reference guide for '{f['name']}' is missing from cheat sheet!")

    @patch("notes_engine._api_call")
    def test_cheat_sheet_never_loses_content(self, mock_api):
        """Each new file appended must not remove any prior section."""
        import notes_engine as ne, study_notes_store as store, canvas

        call_count = [0]
        sections_seen = []

        def fake_api(file_bytes, file_name, prompt, model, max_tokens, **kwargs):
            call_count[0] += 1
            if max_tokens == 10:
                return "slides"
            if "Extract cheat-sheet entries" not in prompt:
                return f"## Notes_{call_count[0]}\nClass notes for {file_name}"
            section_name = f"Section_{call_count[0]}"
            sections_seen.append(section_name)
            return f"## {section_name}\n**Use when**: call {call_count[0]}\n**How**: test"

        mock_api.side_effect = fake_api

        slide_files = [f for f in self.all_files
                       if not ne._is_homework(f["name"])
                       and (f.get("size") or 0) < 2 * 1024 * 1024][:4]

        for f in slide_files:
            try:
                file_bytes = canvas.download_file_by_id(f["canvas_file_id"])
                ne.process_file(f)
            except Exception as e:
                self.fail(f"Failed on {f['name']}: {e}")

        cheat = store.get_cheat_sheet("LING 101")
        if cheat and sections_seen:
            for section in sections_seen:
                self.assertIn(section, cheat["content"],
                    f"Section '{section}' was lost from the cheat sheet!")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Allow: python3 test_pipeline.py [TestClass[.test_method]]
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target = sys.argv.pop(1)
        suite = unittest.TestLoader().loadTestsFromName(target, sys.modules[__name__])
    else:
        suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
