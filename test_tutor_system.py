"""Tutor system tests: PDF export, save/title helpers, conversation memory, and
the background grade-mistake learning pipeline.

Run all:   python3 test_tutor_system.py
Run one:   python3 test_tutor_system.py TestPdfExport.test_table_renders_as_table
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _temp_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    return Path(path)


def _mock_stream(text: str) -> MagicMock:
    """A MagicMock usable as `with client.messages.stream(...) as s: s.get_final_text()`."""
    cm = MagicMock()
    cm.__enter__.return_value.get_final_text.return_value = text
    cm.__exit__.return_value = False
    return cm


# ─────────────────────────────────────────────────────────────────────────────
# TestPdfExport — pure rendering logic, no network, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestPdfExport(unittest.TestCase):

    def test_output_is_a_valid_pdf(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# Title\n\nSome body text.")
        self.assertTrue(data.startswith(b"%PDF"))
        reader = PdfReader(io.BytesIO(data))
        self.assertGreaterEqual(len(reader.pages), 1)

    def test_empty_input_still_produces_a_pdf(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("")
        reader = PdfReader(io.BytesIO(data))
        self.assertGreaterEqual(len(reader.pages), 1)

    def test_headers_stripped_of_hash_marks(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# Big Title\n\n## Sub Section\n\nBody.")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("Big Title", text)
        self.assertIn("Sub Section", text)
        self.assertNotIn("#", text)

    def test_bold_italic_and_code_markup_is_not_leaked_as_literal_text(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf(
            "# T\n\nThis is **bold**, this is *italic*, and this is `range(1, n)`."
        )
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("bold", text)
        self.assertIn("italic", text)
        self.assertIn("range(1, n)", text)
        self.assertNotIn("**", text)
        self.assertNotIn("`", text)

    def test_table_renders_as_a_reportlab_table_flowable(self):
        import pdf_export
        from reportlab.platypus import Table

        markdown = (
            "# T\n\n"
            "| Concept | Formula |\n"
            "|---------|---------|\n"
            "| Sum     | n*(n+1)/2 |\n"
            "| Edge    | n = 0   |\n"
        )
        flow = pdf_export._flowables(markdown, avail_width=400)
        tables = [f for f in flow if isinstance(f, Table)]
        self.assertEqual(len(tables), 1, "Expected exactly one Table flowable for the GFM table")
        # 1 header row + 2 body rows
        self.assertEqual(len(tables[0]._cellvalues), 3)

    def test_table_cell_text_survives_into_the_pdf(self):
        import pdf_export
        from pypdf import PdfReader

        markdown = (
            "# T\n\n"
            "| Concept | Formula |\n"
            "|---------|---------|\n"
            "| Sum of 1..n | n*(n+1)/2 |\n"
        )
        data = pdf_export.markdown_to_pdf(markdown)
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("Concept", text)
        self.assertIn("Sum of 1..n", text)
        self.assertIn("n*(n+1)/2", text)

    def test_lines_with_pipes_but_no_separator_row_are_not_mistaken_for_a_table(self):
        import pdf_export
        from reportlab.platypus import Table

        markdown = "# T\n\nJust a sentence with a | pipe character in it, not a table."
        flow = pdf_export._flowables(markdown, avail_width=400)
        self.assertFalse(any(isinstance(f, Table) for f in flow))

    def test_bullet_list_renders_all_items(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# T\n\n- First item\n- Second item\n- Third item\n")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        for item in ("First item", "Second item", "Third item"):
            self.assertIn(item, text)

    def test_numbered_list_renders_all_items(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# T\n\n1. Step one\n2. Step two\n3. Step three\n")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        for item in ("Step one", "Step two", "Step three"):
            self.assertIn(item, text)

    def test_fenced_code_block_preserved(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# T\n\n```\ndef f(x):\n    return x + 1\n```\n")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("def f(x):", text)

    def test_link_renders_visible_text(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# T\n\nSee [Python docs](https://docs.python.org/3/) for details.")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("Python docs", text)

    def test_xml_special_characters_are_escaped_safely(self):
        import pdf_export

        # Must not raise — reportlab would error on unescaped <, >, & in Paragraph markup.
        data = pdf_export.markdown_to_pdf("# T\n\nCompare a < b && b > c, and A & B.")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_triple_asterisk_bold_italic_does_not_crash(self):
        """Regression: ***bold italic*** used to produce overlapping <b><i>..</b></i>
        tags that crashed reportlab's parser (ValueError: saw </b> instead of </i>)."""
        import pdf_export
        from pypdf import PdfReader

        md = "# T\n\n- Acquire the ***Contemporary Linguistics*** textbook if you haven't.\n"
        data = pdf_export.markdown_to_pdf(md)
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("Contemporary Linguistics", text)
        self.assertNotIn("*", text)

    def test_triple_underscore_bold_italic_does_not_crash(self):
        import pdf_export
        from pypdf import PdfReader

        data = pdf_export.markdown_to_pdf("# T\n\nThis is ___very important___ to know.")
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        self.assertIn("very important", text)
        self.assertNotIn("_", text)

    def test_pathological_overlapping_emphasis_falls_back_instead_of_crashing(self):
        """Even markup no regex fix anticipates must degrade gracefully, never raise."""
        import pdf_export

        md = "# T\n\n*italic **and bold* still going** more text\n"
        data = pdf_export.markdown_to_pdf(md)  # must not raise
        self.assertTrue(data.startswith(b"%PDF"))

    def test_safe_paragraph_falls_back_when_inline_produces_malformed_markup(self):
        """Force the except branch directly: whatever _inline() ever produces,
        a Paragraph must come back rather than an exception propagating."""
        import pdf_export
        from reportlab.platypus import Paragraph

        with patch.object(pdf_export, "_inline", return_value="<b>unclosed <i>tags</b>"):
            para = pdf_export._safe_paragraph("irrelevant, _inline is mocked", pdf_export._BODY)
        self.assertIsInstance(para, Paragraph)


# ─────────────────────────────────────────────────────────────────────────────
# TestTutorFilesHelpers — title/filename helpers, no network, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestTutorFilesHelpers(unittest.TestCase):

    def test_filename_for_uses_pdf_extension(self):
        import tutor_files
        self.assertTrue(tutor_files.filename_for("COMP 110 Cheat Sheet").endswith(".pdf"))

    def test_filename_for_sanitizes_punctuation(self):
        import tutor_files
        name = tutor_files.filename_for("COMP 110: Midterm/Final?!")
        self.assertNotIn(":", name)
        self.assertNotIn("/", name)
        self.assertNotIn("?", name)

    def test_title_from_prompt_variants(self):
        import tutor_files
        self.assertEqual(tutor_files.title_from_prompt("COMP 110", "midterm cheat sheet please"),
                          "COMP 110 Midterm Cheat Sheet")
        self.assertEqual(tutor_files.title_from_prompt("COMP 110", "make a final study guide"),
                          "COMP 110 Final Study Guide")
        self.assertEqual(tutor_files.title_from_prompt("COMP 110", "give me a practice test"),
                          "COMP 110 Practice Test")
        self.assertEqual(tutor_files.title_from_prompt("COMP 110", "something unrelated"),
                          "COMP 110 Study Guide")

    def test_fallback_title_includes_course(self):
        import tutor_files
        self.assertTrue(tutor_files.fallback_title("COMP 110").startswith("COMP 110 Saved Response"))

    def test_markdown_document_avoids_duplicate_title(self):
        import tutor_files
        body_with_own_title = "# My Own Title\n\nContent here."
        result = tutor_files.markdown_document("Ignored Title", body_with_own_title)
        self.assertEqual(result.count("# "), 1, "Should not add a second H1 when the body already has one")
        self.assertIn("My Own Title", result)

    def test_markdown_document_adds_title_when_missing(self):
        import tutor_files
        result = tutor_files.markdown_document("Injected Title", "Plain content, no header.")
        self.assertTrue(result.startswith("# Injected Title"))

    def test_programming_source_help_is_not_auto_file_request(self):
        import tutor_files

        prompt = "write a python script file that cleans this csv"
        self.assertTrue(tutor_files.is_programming_help(prompt))
        self.assertFalse(tutor_files.wants_file(prompt))

    def test_programming_study_document_can_still_be_a_file(self):
        import tutor_files

        self.assertTrue(tutor_files.wants_file("make a Python cheat sheet for my exam"))


# ─────────────────────────────────────────────────────────────────────────────
# TestTutorMessages — auto-saved conversation memory, isolated temp DB
# ─────────────────────────────────────────────────────────────────────────────

class TestTutorMessages(unittest.TestCase):

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

    def test_log_and_load_session_preserves_order_and_roles(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "s1", "user", "What's due Friday?")
        store.log_tutor_message("COMP 110", "s1", "assistant", "PS3 is due Friday.")
        msgs = store.load_tutor_session("COMP 110", "s1")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "What's due Friday?")

    def test_list_tutor_sessions_orders_most_recent_first_and_has_preview(self):
        import time
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "older", "user", "First question")
        time.sleep(1.1)  # created_at has second resolution — force a real ordering gap
        store.log_tutor_message("COMP 110", "newer", "user", "Second question")

        sessions = store.list_tutor_sessions("COMP 110")
        self.assertEqual(sessions[0]["session_id"], "newer")
        self.assertEqual(sessions[1]["session_id"], "older")
        self.assertEqual(sessions[0]["preview"], "Second question")

    def test_sessions_are_scoped_per_course(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "s1", "user", "hello")
        store.log_tutor_message("PHIL 101", "s2", "user", "hi")
        self.assertEqual(len(store.list_tutor_sessions("COMP 110")), 1)
        self.assertEqual(len(store.list_tutor_sessions("PHIL 101")), 1)

    def test_recent_tutor_context_excludes_current_session(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "past", "user", "Old question about loops")
        store.log_tutor_message("COMP 110", "past", "assistant", "Old answer about loops")
        store.log_tutor_message("COMP 110", "live", "user", "This should not appear")

        context = store.recent_tutor_context("COMP 110", exclude_session_id="live")
        self.assertIn("Old question about loops", context)
        self.assertNotIn("This should not appear", context)

    def test_recent_tutor_context_empty_when_no_other_sessions(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "live", "user", "Only message")
        context = store.recent_tutor_context("COMP 110", exclude_session_id="live")
        self.assertEqual(context, "")

    def test_search_tutor_messages_finds_matching_content_only(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "s1", "user", "Make me a midterm cheat sheet")
        store.log_tutor_message("COMP 110", "s1", "assistant", "# COMP 110 Midterm Cheat Sheet\n...")
        store.log_tutor_message("COMP 110", "s2", "user", "What's the weather like")

        hits = store.search_tutor_messages("COMP 110", "cheat sheet")
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("cheat sheet" in h["content"].lower() for h in hits))

    def test_search_tutor_messages_empty_query_returns_nothing(self):
        import study_notes_store as store
        store.log_tutor_message("COMP 110", "s1", "user", "hello")
        self.assertEqual(store.search_tutor_messages("COMP 110", "   "), [])

    def test_save_tutor_file_roundtrips_pdf_bytes_exactly(self):
        import study_notes_store as store
        import pdf_export
        import tutor_files

        title = "COMP 110 Cheat Sheet"
        pdf_bytes = pdf_export.markdown_to_pdf(tutor_files.markdown_document(title, "## Topic\nContent"))

        file_id = store.save_tutor_file(
            "COMP 110", title, tutor_files.filename_for(title), pdf_bytes,
            prompt="make a cheat sheet", mime_type="application/pdf",
        )
        row = store.get_tutor_file(file_id)
        self.assertEqual(row["content"], pdf_bytes)
        self.assertEqual(row["mime_type"], "application/pdf")
        self.assertTrue(row["file_name"].endswith(".pdf"))

        store.delete_tutor_file(file_id)
        self.assertIsNone(store.get_tutor_file(file_id))


# ─────────────────────────────────────────────────────────────────────────────
# TestMistakeLearningPipeline — background grade-feedback learning, mocked I/O
# ─────────────────────────────────────────────────────────────────────────────

class TestMistakeLearningPipeline(unittest.TestCase):

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

    def _assignment(self, **overrides):
        base = {
            "course": "COMP 110", "course_id": 42, "title": "PS4",
            "canvas_id": "999", "canvas_submission_status": "graded",
            "points_possible": 10, "score": 7,
        }
        base.update(overrides)
        return base

    @patch("canvas.submission_feedback")
    def test_sync_graded_work_learns_from_feedback(self, mock_feedback):
        import notes_engine as ne
        import study_notes_store as store

        mock_feedback.return_value = {
            "score": 7, "grade": "7",
            "comments": ["You keep assuming the base case is n=1 instead of n=0."],
            "rubric_comments": [], "body": "", "attachments": [],
        }
        lesson = "## Wrong base case\n**What happened**: used n=1.\n**Why it's wrong**: skips n=0.\n**Catch it next time**: check the domain.\n"
        with patch.object(ne, "_CLIENT") as mock_client:
            mock_client.messages.stream.return_value = _mock_stream(lesson)
            added, skipped = ne.sync_graded_work([self._assignment()])

        self.assertEqual((added, skipped), (1, 0))
        notes = store.get_mistake_notes("COMP 110")
        self.assertIn("Wrong base case", notes["content"])
        self.assertEqual(store.get_graded_work("999")["score"], 7.0)

    @patch("canvas.submission_feedback")
    def test_sync_graded_work_skips_unchanged_score(self, mock_feedback):
        import notes_engine as ne

        mock_feedback.return_value = {
            "score": 7, "grade": "7", "comments": ["Some feedback."],
            "rubric_comments": [], "body": "", "attachments": [],
        }
        with patch.object(ne, "_CLIENT") as mock_client:
            mock_client.messages.stream.return_value = _mock_stream("NOTHING_NEW")
            ne.sync_graded_work([self._assignment()])
            mock_feedback.reset_mock()
            added, skipped = ne.sync_graded_work([self._assignment()])  # same score again

        mock_feedback.assert_not_called()
        self.assertEqual((added, skipped), (0, 0))

    @patch("canvas.submission_feedback")
    def test_sync_graded_work_reprocesses_on_regrade(self, mock_feedback):
        import notes_engine as ne

        mock_feedback.return_value = {
            "score": 7, "grade": "7", "comments": ["Feedback."],
            "rubric_comments": [], "body": "", "attachments": [],
        }
        with patch.object(ne, "_CLIENT") as mock_client:
            mock_client.messages.stream.return_value = _mock_stream("NOTHING_NEW")
            ne.sync_graded_work([self._assignment(score=7)])
            mock_feedback.reset_mock()
            ne.sync_graded_work([self._assignment(score=9)])  # regraded

        mock_feedback.assert_called_once()

    @patch("canvas.submission_feedback")
    def test_sync_graded_work_marks_processed_without_lesson_when_no_feedback(self, mock_feedback):
        import notes_engine as ne
        import study_notes_store as store

        mock_feedback.return_value = None  # e.g. a bare numeric grade, no comments
        added, skipped = ne.sync_graded_work([self._assignment()])

        self.assertEqual((added, skipped), (0, 0))
        self.assertIsNotNone(store.get_graded_work("999"))
        self.assertIsNone(store.get_mistake_notes("COMP 110"))

    def test_sync_graded_work_ignores_ungraded_assignments(self):
        import notes_engine as ne
        import study_notes_store as store

        added, skipped = ne.sync_graded_work([self._assignment(canvas_submission_status="unsubmitted")])
        self.assertEqual((added, skipped), (0, 0))
        self.assertIsNone(store.get_graded_work("999"))

    @patch("canvas.download_file")
    def test_extract_submission_text_skips_oversized_attachment(self, mock_download):
        import notes_engine as ne
        attachments = [{"name": "huge.py", "url": "fake://huge", "size": 999_999_999}]
        result = ne._extract_submission_text(attachments)
        self.assertEqual(result, "")
        mock_download.assert_not_called()

    @patch("canvas.download_file")
    def test_extract_submission_text_swallows_download_failure(self, mock_download):
        import notes_engine as ne
        mock_download.side_effect = RuntimeError("network down")
        attachments = [{"name": "ps.py", "url": "fake://ps.py", "size": 100}]
        result = ne._extract_submission_text(attachments)
        self.assertEqual(result, "")

    @patch("canvas.download_file")
    def test_extract_submission_text_reads_supported_code_file(self, mock_download):
        import notes_engine as ne
        mock_download.return_value = b"def f(n):\n    return n\n"
        attachments = [{"name": "ps.py", "url": "fake://ps.py", "size": 100}]
        result = ne._extract_submission_text(attachments)
        self.assertIn("def f(n):", result)


if __name__ == "__main__":
    unittest.main()
