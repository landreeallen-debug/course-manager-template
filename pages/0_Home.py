"""Home dashboard for upcoming coursework."""
from __future__ import annotations

import datetime as dt

import streamlit as st

import assignments_store
import backup
import canvas
import config
import study_notes_store
import upcoming
from ui_style import apply_blue_theme

apply_blue_theme()
assignments_store.init_db()
study_notes_store.init_db()


def _due_date(due_at: str | None) -> dt.date | None:
    if not due_at:
        return None
    try:
        d = dt.datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(config.LOCAL_TZ)
        return d.date()
    except ValueError:
        try:
            return dt.datetime.strptime(due_at[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _due_label(due: dt.date | None, today: dt.date) -> str:
    if due is None:
        return "No date"
    delta = (due - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    return due.strftime("%a, %b %d")


def _bucket(item: dict, today: dt.date) -> str:
    due = _due_date(item.get("due_at"))
    if due is None:
        return "No Date"
    if due < today and not item.get("done"):
        return "Overdue"
    if due == today:
        return "Today"
    if due <= today + dt.timedelta(days=7):
        return "This Week"
    return "Later"


def _source_label(source: str | None) -> str:
    source = source or "manual"
    if "canvas" in source and "syllabus" in source:
        return "Canvas + syllabus"
    if "canvas" in source:
        return "Canvas"
    if "syllabus" in source:
        return "Syllabus"
    return source.title()


def _status_label(item: dict) -> str:
    if item.get("canvas_submitted"):
        return "Submitted in Canvas"
    if item.get("canvas_missing"):
        return "Missing in Canvas"
    return _source_label(item.get("source"))


def _assignment_rows(show_done: bool) -> list[dict]:
    today = dt.date.today()
    rows = []
    for item in assignments_store.list_assignments():
        due = _due_date(item.get("due_at"))
        if item.get("done") and not show_done:
            continue
        if due and due < today - dt.timedelta(days=30) and not item.get("done"):
            continue
        if due and due > today + dt.timedelta(days=210):
            continue
        rows.append(item)
    return rows


def _render_assignment(item: dict, today: dt.date) -> None:
    due = _due_date(item.get("due_at"))
    overdue = due is not None and due < today and not item.get("done")
    done_now = bool(item.get("done"))
    submitted_by_canvas = bool(item.get("canvas_submitted"))

    with st.container(border=True):
        c_done, c_course, c_title, c_due, c_source = st.columns([0.7, 1.4, 5.1, 1.5, 1.5])
        checked = c_done.checkbox(
            "Done",
            value=done_now,
            key=f"home_done_{item['id']}",
            label_visibility="collapsed",
            disabled=submitted_by_canvas,
        )
        if not submitted_by_canvas and checked != done_now:
            assignments_store.set_done(item["id"], checked)
            st.rerun()

        c_course.markdown(f"<span class='cm-pill'>{item.get('course') or 'Course'}</span>", unsafe_allow_html=True)
        title = item.get("title") or "Untitled"
        title_html = f"<span class='cm-strike'>{title}</span>" if done_now else title
        c_title.markdown(f"<div class='cm-task-title'>{title_html}</div>", unsafe_allow_html=True)

        due_class = "cm-due cm-due-overdue" if overdue else "cm-due"
        c_due.markdown(f"<span class='{due_class}'>{_due_label(due, today)}</span>", unsafe_allow_html=True)
        c_source.markdown(
            f"<span class='cm-source'>{_status_label(item)}</span>",
            unsafe_allow_html=True,
        )


sync_now = False
if canvas.is_configured() and not st.session_state.get("home_synced"):
    sync_now = True

hero_left, hero_right = st.columns([4, 1.4])
with hero_left:
    st.markdown(
        f"""
<div class="cm-home-hero">
  <div class="cm-kicker">Course Manager</div>
  <h1>{upcoming.greeting()}</h1>
  <p>Your day, your classes, and the work that needs attention.</p>
</div>
        """,
        unsafe_allow_html=True,
    )
with hero_right:
    st.markdown("<div class='cm-home-actions'>", unsafe_allow_html=True)
    if st.button("Refresh Canvas", type="primary", use_container_width=True, disabled=not canvas.is_configured()):
        sync_now = True
    st.download_button(
        "Export backup",
        data=backup.build_backup_zip(),
        file_name="course_manager_backup.zip",
        mime="application/zip",
        use_container_width=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

if sync_now:
    with st.spinner("Refreshing Canvas..."):
        st.session_state.home_sync_result = upcoming.sync_from_canvas()
        st.session_state.home_synced = True

sync_result = st.session_state.get("home_sync_result")
if not canvas.is_configured():
    st.info("Canvas is not connected yet.")
elif sync_result:
    if sync_result.get("status") == "ok":
        st.caption(
            f"Canvas refreshed: {sync_result.get('courses', 0)} classes, "
            f"{sync_result.get('canvas_added', 0)} Canvas item(s), "
            f"{sync_result.get('syllabus_added', 0)} syllabus item(s)."
        )
    elif sync_result.get("status") == "partial":
        with st.expander("Canvas refreshed with notes", expanded=False):
            st.write(
                f"{sync_result.get('courses', 0)} classes, "
                f"{sync_result.get('canvas_added', 0)} Canvas item(s), "
                f"{sync_result.get('syllabus_added', 0)} syllabus item(s)."
            )
            for err in sync_result.get("errors", []):
                st.warning(err)
    elif sync_result.get("message"):
        st.warning(sync_result["message"])

today = dt.date.today()
items = _assignment_rows(show_done=False)
done_items = [a for a in assignments_store.list_assignments() if a.get("done")]
due_today = [a for a in items if _due_date(a.get("due_at")) == today]
due_week = [
    a for a in items
    if (d := _due_date(a.get("due_at"))) is not None and today <= d <= today + dt.timedelta(days=7)
]
overdue = [
    a for a in items
    if (d := _due_date(a.get("due_at"))) is not None and d < today
]

s1, s2, s3, s4 = st.columns(4)
s1.markdown(f"<div class='cm-stat'><span>{len(items)}</span><small>Open</small></div>", unsafe_allow_html=True)
s2.markdown(f"<div class='cm-stat'><span>{len(due_today)}</span><small>Today</small></div>", unsafe_allow_html=True)
s3.markdown(f"<div class='cm-stat'><span>{len(due_week)}</span><small>This week</small></div>", unsafe_allow_html=True)
s4.markdown(f"<div class='cm-stat'><span>{len(overdue)}</span><small>Overdue</small></div>", unsafe_allow_html=True)

st.markdown("## Coming Up")
show_done = st.toggle("Show completed", value=False)
display_items = _assignment_rows(show_done=show_done)

if not display_items:
    st.markdown(
        """
<div class="cm-empty">
  <strong>Nothing due soon.</strong>
  <span>Refresh Canvas after instructors post new work.</span>
</div>
        """,
        unsafe_allow_html=True,
    )
else:
    order = ["Overdue", "Today", "This Week", "Later", "No Date"]
    for label in order:
        rows = [a for a in display_items if _bucket(a, today) == label]
        if not rows:
            continue
        st.markdown(f"### {label}")
        for item in rows:
            _render_assignment(item, today)

if done_items and not show_done:
    st.caption(f"{len(done_items)} completed item(s) hidden.")
