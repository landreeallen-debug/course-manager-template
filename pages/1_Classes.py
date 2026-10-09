"""Classes — one tab per active Canvas course.

Each course tab contains three sub-tabs:
  Assignments  — Canvas assignments with due dates and Google Calendar push
  Study Notes  — auto-generated class notes and process cheat sheet
  Tutor        — chat interface grounded in this course's lecture material

Canvas files are synced once per session on first load. Only new files trigger
note generation — already-reviewed files are never re-read.
"""
from __future__ import annotations

import datetime as dt
import importlib
import os
import uuid
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv

import assignments_store
import canvas
import config
import notes_engine
import pdf_export
import study_notes_store as store
import tutor_files
from ui_style import apply_blue_theme

load_dotenv()
apply_blue_theme()

if not hasattr(notes_engine, "needs_processing"):
    notes_engine = importlib.reload(notes_engine)
if not hasattr(store, "list_tutor_sessions"):
    store = importlib.reload(store)
if not hasattr(pdf_export, "_safe_paragraph"):
    pdf_export = importlib.reload(pdf_export)

store.init_db()
assignments_store.init_db()

if not canvas.is_configured():
    st.title("Classes")
    st.warning(
        "Canvas isn't connected yet. Add your access token to `canvas_config.json` "
        "or set the `CANVAS_TOKEN` environment variable."
    )
    st.stop()

if not os.getenv("ANTHROPIC_API_KEY"):
    st.error("No `ANTHROPIC_API_KEY` found. Add it to your `.env` file.")
    st.stop()

_AI = anthropic.Anthropic()

# Tutor chat model routing: Fable 5 for real questions (biggest reasoning/output
# budget, for full practice sets/essays/study guides), Haiku for throwaway
# one-liners ("thanks", "ok") where a big model is wasted spend.
TUTOR_MODEL = "claude-fable-5"
TUTOR_FALLBACK_MODEL = "claude-opus-4-8"  # server-side fallback if Fable 5 declines
SHORT_REPLY_MODEL = "claude-haiku-4-5"
SHORT_PROMPT_CHARS = 20

# ── Course list (cheap — fetched every run) ─────────────────────────────────
#
# Only the course list itself is fetched up front. A course's assignments,
# syllabus/pages/announcements, and file listing are fetched from Canvas the
# first time that course is selected in a session, not for every course at
# once. Streamlit renders every st.tabs() child on every rerun even when
# hidden, so syncing (or just rendering) all courses eagerly meant a Canvas
# problem with one course — or just the normal cost of rendering it — showed
# up no matter which course tab you were actually looking at.

try:
    courses = canvas.active_courses()
except Exception as e:
    st.title("Classes")
    st.error(f"Couldn't load your Canvas courses: {e}")
    st.stop()

st.session_state.active_courses = courses
st.session_state.setdefault("canvas_files", [])
st.session_state.setdefault("synced_course_codes", set())

if not courses:
    st.title("Classes")
    st.info("No active courses found in Canvas.")
    st.stop()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _due_date(due_at):
    if not due_at:
        return None
    try:
        d = dt.datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(config.LOCAL_TZ)
        return d.date()
    except ValueError:
        return None


def _sync_course(course: dict) -> None:
    """Pull one course's assignments, syllabus/pages/announcements, and file list from Canvas.

    Runs once per course per session, the first time that course is selected —
    not for every course up front.
    """
    code = course["code"]
    if code in st.session_state.synced_course_codes:
        return
    with st.status(f"Syncing {code} from Canvas…", expanded=True) as status:
        try:
            status.write("Fetching assignments…")
            course_assignments = [a for a in canvas.assignments() if a.get("course") == code]
            for a in course_assignments:
                assignments_store.add_assignment(
                    course=a["course"], title=a["title"], type="assignment",
                    due_at=a.get("due_at"), source="canvas", canvas_id=a.get("canvas_id"),
                    canvas_submitted=bool(a.get("canvas_submitted")),
                    canvas_submission_status=a.get("canvas_submission_status"),
                    canvas_submitted_at=a.get("canvas_submitted_at"),
                    canvas_missing=bool(a.get("canvas_missing")),
                )
            try:
                # Learn from grader feedback on newly-graded work so the tutor can
                # flag repeated logic errors instead of just answering questions.
                notes_engine.sync_graded_work(course_assignments)
            except Exception:
                pass  # non-fatal — tutor still works without mistake tracking

            status.write("Listing course files…")
            files = [
                {**f, "course": code, "course_name": course.get("name", "")}
                for f in canvas.course_files(course["id"])
            ]
            status.write("Fetching syllabus, pages, and announcements…")
            try:
                knowledge = canvas.fetch_course_knowledge(course["id"], code)
                if knowledge:
                    store.upsert_course_knowledge(code, knowledge)
            except Exception:
                pass  # non-fatal — tutor still works without it
            try:
                linked = canvas.linked_course_files(course["id"], code, course.get("name", ""))
            except Exception:
                linked = []
            if linked:
                by_id = {f["canvas_file_id"]: f for f in files}
                for f in linked:
                    by_id[f["canvas_file_id"]] = f
                files = list(by_id.values())

            status.write("Extracting file contents…")
            try:
                added, skipped = notes_engine.sync_course_materials(files)
                store.upsert_sync_status(
                    code, "ok", "Canvas sync complete.",
                    files_seen=len(files), files_added=added, files_skipped=skipped,
                )
            except Exception as e:
                store.upsert_sync_status(
                    code, "error", "Canvas material extraction failed.",
                    files_seen=len(files), errors=[str(e)],
                )

            by_id = {f["canvas_file_id"]: f for f in st.session_state.canvas_files}
            for f in files:
                by_id[f["canvas_file_id"]] = f
            st.session_state.canvas_files = list(by_id.values())
            st.session_state.synced_course_codes.add(code)
            status.update(label=f"Synced {code}", state="complete", expanded=False)
        except Exception as e:
            status.update(label=f"Canvas sync failed for {code}", state="error")
            st.error(f"Canvas sync failed for {code}: {e}")


def _assignments_tab(course_code: str) -> None:
    """Render the Assignments sub-tab for one course."""
    items = [a for a in assignments_store.list_assignments() if a.get("course") == course_code]
    today = dt.date.today()

    if not items:
        st.caption("No assignments yet — they're imported from Canvas automatically on page load.")
        return

    def status_label(a: dict) -> str:
        if a.get("canvas_submitted"):
            return "[submitted in Canvas]"
        if a.get("canvas_missing"):
            return "[missing in Canvas]"
        if "canvas" in (a.get("source") or "") and a.get("canvas_submission_status"):
            return f"[Canvas: {a['canvas_submission_status']}]"
        return {"canvas": "[canvas]", "syllabus": "[syllabus]"}.get(a["source"], "•")

    buckets = {"Overdue": [], "This week": [], "Later": [], "No date": []}
    for a in items:
        d = _due_date(a["due_at"])
        if d is None:
            buckets["No date"].append(a)
        elif d < today and not a["done"]:
            buckets["Overdue"].append(a)
        elif d <= today + dt.timedelta(days=7):
            buckets["This week"].append(a)
        else:
            buckets["Later"].append(a)

    for label, rows in buckets.items():
        if not rows:
            continue
        st.subheader(f"{label} ({len(rows)})")
        for a in rows:
            c1, c2 = st.columns([1, 12])
            submitted_by_canvas = bool(a.get("canvas_submitted"))
            done = c1.checkbox(
                "Done",
                value=bool(a["done"]),
                key=f"done_{a['id']}",
                label_visibility="collapsed",
                disabled=submitted_by_canvas,
            )
            if not submitted_by_canvas and done != bool(a["done"]):
                assignments_store.set_done(a["id"], done)
                st.rerun()
            d       = _due_date(a["due_at"])
            due_str = d.strftime("%a %b %d") if d else "no date"
            title   = f"~~{a['title']}~~" if a["done"] else f"**{a['title']}**"
            badge   = status_label(a)
            line, btn = c2.columns([10, 1])
            line.markdown(f"{title} · {due_str} · {badge}")
            if btn.button(
                "",
                icon=":material/delete:",
                key=f"del_a_{a['id']}",
                help="Delete this assignment",
                type="tertiary",
                width="content",
            ):
                assignments_store.delete(a["id"])
                st.rerun()


def _sort_pending(files: list[dict]) -> list[dict]:
    """Slides always before homework — delegates to notes_engine.sort_pending."""
    return notes_engine.sort_pending(files)


def _collect_practice_materials(
    hw_name: str, course_files: list[dict], status_placeholder
) -> list[tuple[str, str]] | None:
    """Download answer keys and practice files relevant to a homework.

    Looks for:
      1. The homework's own answer key (e.g. HW1 → HW_1_KEY)
      2. Practice exercise answer files (e.g. ch2_ex answers)
    Returns a list of (label, extracted_text) pairs — skips files > 3 MB.
    """
    import re as _re

    hw_base = hw_name.lower()
    m       = _re.search(r'hw\s*(\d+)', hw_base)
    hw_num  = m.group(1) if m else ""

    results = []
    for f in course_files:
        if (f.get("size") or 0) > 3 * 1024 * 1024:
            continue
        fname          = f["name"].lower()
        is_key         = "key" in fname or "answer" in fname or "soln" in fname
        is_hw_key      = is_key and hw_num and f"hw{hw_num}" in fname.replace("_", "").replace(" ", "")
        is_practice_ans = is_key and "hw" not in fname

        if not (is_hw_key or is_practice_ans):
            continue

        label = f"Answer key: {f['name']}" if is_hw_key else f"Practice answers: {f['name']}"
        status_placeholder.markdown(f"Fetching {f['name']}...")
        try:
            fb  = canvas.download_file_by_id(f["canvas_file_id"])
            txt = notes_engine.extract_text(fb, f["name"])
            if txt.strip():
                results.append((label, txt))
        except Exception:
            pass  # optional context — skip silently

    return results if results else None


def _notes_tab(course_code: str, course_files: list[dict]) -> None:
    """Render the Study Notes sub-tab for one course."""
    raw_pending  = [f for f in course_files if notes_engine.needs_processing(f)]
    pending      = _sort_pending(raw_pending)   # slides first, homework last
    manual_docs  = [
        m for m in store.list_course_materials(course_code)
        if str(m["canvas_file_id"]).startswith("manual:")
    ]
    # Manually-uploaded documents skip the review pipeline (they're added straight
    # to tutor knowledge), but they're still "considered" — count them as such so
    # the header reflects everything the tutor actually knows about.
    already          = len(course_files) - len(pending) + len(manual_docs)
    total_considered = len(course_files) + len(manual_docs)

    h1, h2, h3, h4 = st.columns([5, 1, 1, 1])
    if h2.button("Refresh", key=f"refresh_{course_code}", help="Re-check Canvas for new files"):
        st.session_state.synced_course_codes.discard(course_code)
        st.rerun()

    if h3.button("Rebuild", key=f"rebuild_{course_code}", help="Rebuild cheat sheet from existing notes (fast — no re-download)"):
        with st.spinner("Rebuilding cheat sheet from class notes…"):
            new_sheet = notes_engine.rebuild_cheat_from_notes(course_code)
            store.upsert_cheat_sheet(course_code, new_sheet)
            # Reset only homework-like files so their reference guides regenerate.
            for f in course_files:
                if notes_engine._is_homework(f["name"]):
                    store.clear_processed_file(f["canvas_file_id"])
        st.success("Cheat sheet rebuilt. Hit Review to add homework reference guides.")
        st.rerun()

    if h4.button("Regen", key=f"regen_{course_code}", help="Regenerate everything from scratch"):
        store.clear_processed_for_course(course_code)
        store.clear_notes_for_course(course_code)
        st.rerun()

    sync_status = store.get_sync_status(course_code)
    if sync_status:
        status_label = "Sync OK" if sync_status["status"] == "ok" else "Sync issue"
        with st.expander(f"{status_label} · {sync_status['synced_at'][:19]}", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("Files seen", sync_status["files_seen"])
            c2.metric("Indexed/updated", sync_status["files_added"])
            c3.metric("Skipped", sync_status["files_skipped"])
            if sync_status.get("message"):
                st.caption(sync_status["message"])
            for err in sync_status.get("errors", []):
                st.warning(err)

    if pending:
        h1.info(
            f"**{len(pending)} new file{'s' if len(pending) != 1 else ''} to review**"
            + (f" · {already} already considered" if already else "")
        )
        if st.button(
            f"Review {len(pending)} new file{'s' if len(pending) != 1 else ''}",
            type="primary", key=f"review_{course_code}"
        ):
            total      = len(pending)
            bar        = st.progress(0.0)
            file_hdr   = st.empty()   # which file
            step_hdr   = st.empty()   # which step within that file
            log        = st.container()

            for i, f in enumerate(pending):
                bar.progress(i / total)
                name = f["name"]
                file_hdr.markdown(
                    f"**File {i+1}/{total}** — `{name}`  ·  {total - i} remaining"
                )

                # ── Step 1: download ─────────────────────────────────────────
                step_hdr.markdown("Downloading...")
                try:
                    file_bytes = canvas.download_file_by_id(f["canvas_file_id"])
                except Exception as e:
                    log.markdown(f"[error] **{name}** — download failed: {e}")
                    step_hdr.empty()
                    continue

                size_kb = f.get("size", 0) // 1024
                if size_kb > notes_engine.AUTO_MAX_BYTES // 1024:
                    store.mark_processed(f["canvas_file_id"], course_code, name, "skip", f.get("updated_at"))
                    log.markdown(f"[skip] **{name}** — too large, skipped")
                    step_hdr.empty()
                    continue

                # ── Step 2: classify ─────────────────────────────────────────
                step_hdr.markdown("Reading & classifying...")
                try:
                    classification = notes_engine.classify(file_bytes, name)
                    classification = notes_engine._normalize_classification(name, classification)
                except Exception as e:
                    log.markdown(f"[error] **{name}** — classify failed: {e}")
                    step_hdr.empty()
                    continue

                if classification == "skip":
                    store.mark_processed(f["canvas_file_id"], course_code, name, "skip", f.get("updated_at"))
                    log.markdown(f"[skip] **{name}** — not slides or homework, skipped")
                    step_hdr.empty()
                    continue

                # ── Step 3: notes (slides only) ───────────────────────────────
                if classification == "slides":
                    step_hdr.markdown("Writing class notes (1-2 min)...")
                    try:
                        store.upsert_course_notes(
                            course_code,
                            notes_engine.update_notes(file_bytes, name, course_code),
                        )
                    except Exception as e:
                        log.markdown(f"[error] **{name}** — notes failed: {e}")
                        step_hdr.empty()
                        continue

                # ── Step 4: cheat sheet ───────────────────────────────────────
                step_hdr.markdown("Updating cheat sheet (1-2 min)...")
                try:
                    doc_type = "lecture slides" if classification == "slides" else "homework assignment"

                    practice_texts = None
                    if classification == "homework":
                        # Find answer keys and practice files for this homework
                        practice_texts = _collect_practice_materials(name, course_files, step_hdr)

                    store.upsert_cheat_sheet(
                        course_code,
                        notes_engine.update_cheat_sheet(
                            file_bytes, name, course_code, doc_type, practice_texts
                        ),
                    )
                except Exception as e:
                    log.markdown(f"[error] **{name}** — cheat sheet failed: {e}")
                    step_hdr.empty()
                    continue

                store.mark_processed(f["canvas_file_id"], course_code, name, classification, f.get("updated_at"))
                prefix = "[notes]" if classification == "slides" else "[hw]"
                log.markdown(f"{prefix} **{name}** — done")
                step_hdr.empty()

            bar.progress(1.0)
            file_hdr.markdown(f"**All {total} files reviewed.**")
            step_hdr.empty()
            st.rerun()
    elif total_considered:
        h1.success(f"All {total_considered} files considered — nothing new to review.")

    st.divider()

    notes_row = store.get_course_notes(course_code)
    sheet_row = store.get_cheat_sheet(course_code)

    if not notes_row and not sheet_row:
        st.caption("No documents yet — review files above to generate notes.")
        return

    # ── Course document upload ────────────────────────────────────────────────
    doc_notice_key = f"doc_upload_notice_{course_code}"
    doc_expanded_key = f"doc_upload_expanded_{course_code}"
    if notice := st.session_state.pop(doc_notice_key, None):
        st.success(notice)

    with st.expander(
        "Upload course documents (syllabus, handouts, etc.)",
        expanded=st.session_state.get(doc_expanded_key, False),
    ):
        st.caption(
            "Upload any PDF the tutor should know about — syllabus, exam schedule, "
            "professor handouts. Text is extracted and added to the tutor's knowledge "
            "and counted in \"files considered\" above."
        )
        doc_file = st.file_uploader(
            "PDF document",
            type=["pdf"],
            key=f"doc_upload_{course_code}",
            label_visibility="collapsed",
        )
        doc_label = st.text_input(
            "What is this?",
            placeholder="e.g. Course syllabus — LING 101 Summer 2026",
            key=f"doc_label_{course_code}",
        )
        if st.button("Add to tutor knowledge", key=f"doc_save_{course_code}") and doc_file:
            if not doc_label.strip():
                st.warning("Give it a label.")
            else:
                with st.spinner("Extracting text..."):
                    text = notes_engine.extract_text(doc_file.getvalue(), doc_file.name)
                if not text.strip():
                    st.error("Couldn't extract text from this PDF.")
                else:
                    material_id = f"manual:{course_code}:{doc_label.strip().lower()}"
                    store.upsert_course_material(
                        material_id,
                        course_code,
                        doc_label.strip(),
                        text,
                        doc_file.name,
                    )
                    st.session_state[doc_notice_key] = (
                        f"Added **{doc_label.strip()}** to tutor knowledge "
                        f"({len(text):,} chars) — it now counts toward files considered."
                    )
                    st.session_state[doc_expanded_key] = True
                    st.rerun()

        # Show what's been added manually
        user_docs = [
            m["file_name"] for m in store.list_course_materials(course_code)
            if str(m["canvas_file_id"]).startswith("manual:")
        ]
        if user_docs:
            st.caption(f"{len(user_docs)} uploaded document(s) already in tutor knowledge:")
            for name in user_docs:
                st.markdown(f"✅ {name}")

    # ── Textbook upload ───────────────────────────────────────────────────────
    with st.expander("Textbook pages", expanded=False):
        st.caption("Upload photos of textbook pages — they'll be included automatically when generating homework reference guides.")
        uploaded_imgs = st.file_uploader(
            "Photos of textbook pages",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"tb_upload_{course_code}",
        )
        label_input = st.text_input("Label for these pages", placeholder="e.g. Chapter 3, pp. 45–52 — IPA consonants", key=f"tb_label_{course_code}")
        if st.button("Save pages", key=f"tb_save_{course_code}") and uploaded_imgs:
            if not label_input.strip():
                st.warning("Add a label so you know what these pages cover.")
            else:
                img_dir = config.data_path("textbook_images") / course_code.replace(" ", "_")
                img_dir.mkdir(parents=True, exist_ok=True)
                for img in uploaded_imgs:
                    import time as _time
                    dest = img_dir / f"{int(_time.time() * 1000)}_{img.name}"
                    dest.write_bytes(img.getvalue())
                    store.add_textbook_page(course_code, label_input.strip(), str(dest))
                st.success(f"Saved {len(uploaded_imgs)} image(s) as '{label_input.strip()}'.")
                st.rerun()

        pages = store.list_textbook_pages(course_code)
        if pages:
            st.caption(f"{len(pages)} page(s) stored — included in all future homework guides.")
            for p in pages:
                c1, c2 = st.columns([8, 1])
                c1.markdown(f"**{p['label']}** · `{Path(p['file_path']).name}`")
                if c2.button(
                    "",
                    icon=":material/delete:",
                    key=f"tb_del_{p['id']}",
                    help="Delete this textbook page",
                    type="tertiary",
                    width="content",
                ):
                    fp = store.delete_textbook_page(p["id"])
                    if fp:
                        try:
                            Path(fp).unlink(missing_ok=True)
                        except Exception:
                            pass
                    st.rerun()

    st.divider()
    t_notes, t_sheet = st.tabs(["Class Notes", "Cheat Sheet"])

    with t_notes:
        if notes_row:
            st.caption(f"Last updated {(notes_row.get('updated_at') or '')[:10]}")
            st.markdown(notes_row["content"])
            st.download_button(
                "Download (PDF)",
                data=pdf_export.markdown_to_pdf(notes_row["content"], pdf_title=f"{course_code} Class Notes"),
                file_name=f"{course_code.replace(' ','_')}_notes.pdf",
                mime="application/pdf", key=f"dl_notes_{course_code}",
            )
        else:
            st.caption("No lecture slides processed yet.")

    with t_sheet:
        if not sheet_row:
            st.caption("No slides or homework processed yet.")
        else:
            st.caption(f"Last updated {(sheet_row.get('updated_at') or '')[:10]}")
            content = sheet_row["content"]

            # Split into methods (slides) vs homework reference guides
            import re as _re
            raw_sections = _re.split(r'\n(?=## )', content)
            methods_parts, hw_guides = [], []
            for sec in raw_sections:
                m = _re.match(r'## (.+?) — (?:Complete )?Reference Guide', sec)
                if m:
                    hw_guides.append((m.group(1).strip(), sec.strip()))
                else:
                    methods_parts.append(sec)

            methods_content = "\n".join(methods_parts)

            if hw_guides:
                # Each homework gets its own sub-tab so it's immediately findable
                labels = ["Methods & Processes"] + [f"{name}" for name, _ in hw_guides]
                sub_tabs = st.tabs(labels)

                with sub_tabs[0]:
                    st.markdown(methods_content)
                    st.download_button(
                        "Download methods (PDF)",
                        data=pdf_export.markdown_to_pdf(methods_content, pdf_title=f"{course_code} Methods"),
                        file_name=f"{course_code.replace(' ','_')}_methods.pdf",
                        mime="application/pdf", key=f"dl_methods_{course_code}",
                    )

                for idx, (hw_name, hw_content) in enumerate(hw_guides):
                    with sub_tabs[idx + 1]:
                        st.markdown(hw_content)
                        st.download_button(
                            "Download this guide (PDF)",
                            data=pdf_export.markdown_to_pdf(hw_content, pdf_title=hw_name),
                            file_name=f"{hw_name.replace(' ','_')}_reference.pdf",
                            mime="application/pdf", key=f"dl_hw_{course_code}_{idx}",
                        )
            else:
                # No homework guides yet — show everything flat
                st.info("No homework reference guides yet — review your homework files to generate them.")
                st.markdown(methods_content)

            st.download_button(
                "Download full cheat sheet (PDF)",
                data=pdf_export.markdown_to_pdf(content, pdf_title=f"{course_code} Cheat Sheet"),
                file_name=f"{course_code.replace(' ','_')}_cheat_sheet.pdf",
                mime="application/pdf", key=f"dl_sheet_{course_code}",
            )


def _build_canvas_context(course_code: str) -> str:
    """Build a Canvas data block for the tutor: assignments, due dates, course info."""
    import datetime as _dt

    today = _dt.date.today()
    lines = [f"CANVAS DATA FOR {course_code} (live as of {today}):"]

    # Course info
    courses = st.session_state.get("active_courses", [])
    for c in courses:
        if c["code"] == course_code:
            lines.append(f"Course: {c['name']} ({c['code']})")
            break

    # Assignments
    items = assignments_store.list_assignments()
    course_items = [a for a in items if a.get("course") == course_code]

    if course_items:
        lines.append("\nAssignments:")
        for a in sorted(course_items, key=lambda x: x["due_at"] or "9999"):
            d = _due_date(a["due_at"])
            due_str = d.strftime("%A, %B %d") if d else "no due date"
            if a.get("canvas_submitted"):
                status = "submitted in Canvas"
            elif a.get("canvas_missing"):
                status = "missing in Canvas"
            else:
                status = "done" if a["done"] else ("overdue" if d and d < today else "upcoming")
            lines.append(f"  - {a['title']} | due: {due_str} | {status}")
    else:
        lines.append("No assignments found.")

    # Canvas files available
    all_files = st.session_state.get("canvas_files", [])
    course_files = [f["name"] for f in all_files if f.get("course") == course_code]
    if course_files:
        lines.append(f"\nCanvas files ({len(course_files)}): " + ", ".join(course_files))

    return "\n".join(lines)


def _render_tutor_files(course_code: str) -> None:
    """Show tutor-generated files for a course."""
    files = store.list_tutor_files(course_code)
    if not files:
        st.caption("No saved tutor files yet.")
        return

    for file in files:
        with st.container(border=True):
            c_info, c_dl, c_del = st.columns([5, 1.4, 1])
            data, download_name, mime_type = _tutor_file_download(file)
            c_info.markdown(f"**{file['title']}**")
            c_info.caption(f"{download_name} - PDF - created {file['created_at'][:19]}")
            c_dl.download_button(
                "Download PDF",
                data=data,
                file_name=download_name,
                mime=mime_type,
                key=f"dl_tutor_file_{file['id']}",
                use_container_width=True,
            )
            if c_del.button("Delete", key=f"del_tutor_file_{file['id']}", use_container_width=True):
                store.delete_tutor_file(file["id"])
                st.rerun()


def _tutor_file_download(file: dict) -> tuple[bytes | str, str, str]:
    """Return PDF download data for new PDF rows and older markdown rows alike."""
    title = file.get("title") or Path(file.get("file_name") or "").stem or "Tutor File"
    file_name = file.get("file_name") or tutor_files.filename_for(title)
    content = file.get("content") or b""

    if isinstance(content, bytes):
        if content.startswith(b"%PDF"):
            download_name = file_name if file_name.lower().endswith(".pdf") else tutor_files.filename_for(title)
            return content, download_name, "application/pdf"
        markdown = content.decode("utf-8", errors="replace")
    else:
        markdown = str(content)

    markdown_content = tutor_files.markdown_document(title, markdown)
    return (
        pdf_export.markdown_to_pdf(markdown_content, pdf_title=title),
        tutor_files.filename_for(title),
        "application/pdf",
    )


def _save_tutor_reply_as_pdf(course_code: str, prompt_text: str, reply: str) -> str:
    """Persist an automatically generated non-programming tutor document as PDF."""
    if not (reply or "").strip():
        return ""
    if reply.startswith("Error:") or reply.startswith("I can't help"):
        return ""

    title = tutor_files.title_from_prompt(course_code, prompt_text)
    markdown_content = tutor_files.markdown_document(title, reply)
    pdf_bytes = pdf_export.markdown_to_pdf(markdown_content, pdf_title=title)
    file_name = tutor_files.filename_for(title)
    store.save_tutor_file(
        course_code,
        title,
        file_name,
        pdf_bytes,
        prompt=prompt_text,
        mime_type="application/pdf",
    )
    return file_name


def _render_save_button(course_code: str, history_key: str, idx: int) -> None:
    """Manual 'save this exact response as a PDF' control — available on every reply."""
    if st.button("Save as PDF", key=f"save_{course_code}_{idx}"):
        messages = st.session_state[history_key]
        prev = messages[idx - 1] if idx > 0 else None
        prompt_text = prev["content"] if prev and prev["role"] == "user" else ""
        title = (
            tutor_files.title_from_prompt(course_code, prompt_text)
            if prompt_text else tutor_files.fallback_title(course_code)
        )
        markdown_content = tutor_files.markdown_document(title, messages[idx]["content"])
        pdf_bytes = pdf_export.markdown_to_pdf(markdown_content, pdf_title=title)
        file_name = tutor_files.filename_for(title)
        store.save_tutor_file(
            course_code, title, file_name, pdf_bytes, prompt=prompt_text, mime_type="application/pdf"
        )
        st.toast(f"Saved as {file_name} — see Tutor files above.")
        st.rerun()


def _render_past_conversations(course_code: str, history_key: str, session_key: str) -> None:
    """Browse or search every past conversation for this course — nothing to save manually."""
    query = st.text_input(
        "Search past chats", key=f"search_{course_code}",
        placeholder="e.g. induction proof, midterm dates...",
    )
    if query:
        hits = store.search_tutor_messages(course_code, query)
        if not hits:
            st.caption("No matches.")
        for h in hits:
            with st.container(border=True):
                st.caption(f"{h['role'].capitalize()} · {h['created_at'][:19]}")
                snippet = h["content"][:500]
                st.markdown(snippet + ("..." if len(h["content"]) > 500 else ""))
        return

    sessions = store.list_tutor_sessions(course_code)
    if not sessions:
        st.caption("No past conversations yet — chats are saved automatically as you go.")
        return
    for s in sessions:
        with st.container(border=True):
            c_info, c_open = st.columns([5, 1.4])
            c_info.markdown(f"**{s['preview'] or '(no preview)'}**")
            c_info.caption(f"{s['message_count']} messages · started {s['started_at'][:19]}")
            if c_open.button(
                "Open", key=f"open_session_{course_code}_{s['session_id']}", use_container_width=True
            ):
                st.session_state[history_key] = [
                    {"role": m["role"], "content": m["content"]}
                    for m in store.load_tutor_session(course_code, s["session_id"])
                ]
                st.session_state[session_key] = s["session_id"]
                st.rerun()


def _tutor_tab(course_code: str, course_name: str) -> None:
    """Render the Tutor chat sub-tab for one course."""
    canvas_context = _build_canvas_context(course_code)

    if not notes_engine.has_tutor_sources(course_code) and "Canvas files" not in canvas_context and "Assignments:" not in canvas_context:
        st.caption("No course materials synced yet — refresh Canvas from the Study Notes tab.")
        return

    history_key = f"tutor_{course_code}"
    session_key = f"tutor_session_{course_code}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    if session_key not in st.session_state:
        st.session_state[session_key] = uuid.uuid4().hex
    session_id = st.session_state[session_key]

    # Header row
    col_title, col_new = st.columns([5, 1])
    col_title.caption(f"Grounded in your {course_code} syllabus, posted files, assignments, and notes — every message is saved automatically.")
    if col_new.button("New conversation", key=f"newconvo_{course_code}"):
        st.session_state[history_key] = []
        st.session_state[session_key] = uuid.uuid4().hex
        st.rerun()

    notice_key = f"tutor_file_notice_{course_code}"
    if notice := st.session_state.pop(notice_key, None):
        if notice.get("type") == "warning":
            st.warning(notice["text"])
        else:
            st.success(notice["text"])

    with st.expander("Tutor files", expanded=False):
        _render_tutor_files(course_code)

    with st.expander("Past conversations", expanded=False):
        _render_past_conversations(course_code, history_key, session_key)

    history_context = store.recent_tutor_context(course_code, exclude_session_id=session_id)
    mistake_notes = store.get_mistake_notes(course_code)

    persona = f"""\
You are a dedicated tutor for {course_code} ({course_name}) at UNC Chapel Hill.
You have course-wide knowledge: Canvas assignments, posted files, syllabus content,
course pages, announcements, assignment descriptions, lecture notes, and cheat sheet.

When answering:
- For exam/midterm/final dates, grading policy, office hours, and course schedule:
  first search syllabus/course information and posted files.
- For assignment due dates and completion status: use the Canvas assignment data.
- For course content, linguistic concepts, and problem-solving: use the lecture notes and cheat sheet.
- For homework help: give direct, complete answers and explain the reasoning/method behind them.
- If the student asks you to make, create, generate, or write a cheat sheet,
  study guide, review sheet, practice test, outline, or downloadable file:
  produce a complete standalone Markdown document. Start with a single H1 title.
  Do not include a preamble like "Sure" or "Here is". Use headings, compact tables,
  checklists, examples, and a "What I could not verify" section when needed.
  The app saves these non-programming files as PDFs automatically.
  If the student is asking for programming help or source code, answer normally
  with code blocks instead of turning the response into a downloadable study file.
  For midterm or final files, synthesize from the syllabus, posted files, class notes,
  cheat sheet, assignment details, and Canvas data. Cover everything the available
  course materials indicate could be relevant, but do not invent unsupported facts.
- If something isn't covered anywhere, say so clearly.
- You silently track this student's past graded mistakes (logic errors,
  misconceptions pulled from real grader feedback) below. Before finishing any
  homework help, practice problem, or explanation, check whether the current
  approach risks one of these same errors. If it does, call it out explicitly
  by name and explain how to avoid it this time — don't just silently avoid the
  mistake yourself.

MEMORY: Every message in this chat is saved automatically — nothing needs to be
manually saved. Past sessions are included below; if asked what was discussed
before, search them and answer specifically."""

    # Scrollable message history
    chat_box = st.container(height=520)
    with chat_box:
        if not st.session_state[history_key]:
            st.caption("Ask about your syllabus, midterms, posted files, lectures, notes, or homework.")
        for idx, msg in enumerate(st.session_state[history_key]):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant":
                    _render_save_button(course_code, history_key, idx)

    # Input pinned below
    if prompt := st.chat_input(f"Ask about {course_code}...", key=f"chat_{course_code}"):
        make_file = tutor_files.wants_file(prompt)
        st.session_state[history_key].append({"role": "user", "content": prompt})
        store.log_tutor_message(course_code, session_id, "user", prompt)

        with chat_box:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                if make_file:
                    context = notes_engine.build_study_file_context(course_code, prompt)
                else:
                    context = notes_engine.build_tutor_context(course_code, prompt)
                system_blocks = [
                    {"type": "text", "text": persona},
                    {"type": "text", "text": canvas_context},
                ]
                if context:
                    system_blocks.append({"type": "text", "text": context, "cache_control": {"type": "ephemeral"}})
                if mistake_notes and mistake_notes.get("content"):
                    system_blocks.append(
                        {"type": "text", "text": mistake_notes["content"], "cache_control": {"type": "ephemeral"}}
                    )
                if history_context:
                    system_blocks.append({"type": "text", "text": history_context})

                api_msgs = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state[history_key]
                ]

                use_fable = len(prompt.strip()) >= SHORT_PROMPT_CHARS
                stream_kwargs = dict(
                    model=TUTOR_MODEL if use_fable else SHORT_REPLY_MODEL,
                    max_tokens=64000 if make_file else 16000,
                    system=system_blocks,
                    messages=api_msgs,
                )
                if use_fable:
                    # Fable 5 thinks by default (no `thinking` param needed) — give it
                    # more effort for a full generated document than a quick answer.
                    stream_kwargs["output_config"] = {"effort": "high" if make_file else "medium"}
                    stream_kwargs["betas"] = ["server-side-fallback-2026-06-01"]
                    stream_kwargs["extra_body"] = {
                        "fallbacks": [{"model": TUTOR_FALLBACK_MODEL}]
                    }

                try:
                    with _AI.beta.messages.stream(**stream_kwargs) as stream:
                        reply = st.write_stream(stream.text_stream)
                        final = stream.get_final_message()
                    if final.stop_reason == "refusal":
                        reply = "I can't help with that request — try rephrasing it."
                        st.warning(reply)
                except Exception as e:
                    reply = f"Error: {e}"
                    st.error(reply)

        st.session_state[history_key].append({"role": "assistant", "content": reply})
        store.log_tutor_message(course_code, session_id, "assistant", reply)
        if make_file:
            try:
                file_name = _save_tutor_reply_as_pdf(course_code, prompt, reply)
            except Exception as e:
                st.session_state[notice_key] = {
                    "type": "warning",
                    "text": f"The response was generated, but PDF saving failed: {e}",
                }
            else:
                if file_name:
                    st.session_state[notice_key] = {
                        "type": "success",
                        "text": f"Saved {file_name} as a PDF. Open Tutor files to download it.",
                    }
        st.rerun()


# ── Main layout ───────────────────────────────────────────────────────────────

st.title("Classes")

st.session_state.setdefault("selected_course_code", None)

if not st.session_state.selected_course_code:
    try:
        grades = canvas.current_grades()
    except Exception:
        grades = {}  # non-fatal — the grid still works without grades

    cols = st.columns(3)
    for i, c in enumerate(courses):
        g = grades.get(c["id"]) or {}
        if g.get("grade"):
            grade_label = g["grade"]
        elif g.get("score") is not None:
            grade_label = f"{g['score']:.1f}%"
        else:
            grade_label = "—"

        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{c['code']}**")
                st.caption(c.get("name") or "")
                st.metric("Current grade", grade_label)
                if st.button("Open", key=f"open_{i}_{c['id']}", use_container_width=True):
                    st.session_state.selected_course_code = c["code"]
                    st.rerun()
    st.stop()

if st.button("← All classes", key="back_to_classes"):
    st.session_state.selected_course_code = None
    st.rerun()

selected_code = st.session_state.selected_course_code
course = next(c for c in courses if c["code"] == selected_code)
_sync_course(course)

files = [f for f in st.session_state.canvas_files if f["course"] == selected_code]

a_tab, n_tab, t_tab = st.tabs(["Assignments", "Study Notes", "Tutor"])
with a_tab:
    _assignments_tab(selected_code)
with n_tab:
    _notes_tab(selected_code, files)
with t_tab:
    _tutor_tab(selected_code, course["name"])
