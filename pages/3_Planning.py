"""Planning — course advisor, graduation plan, and schedule builder.

Three sections, opened one at a time from a home grid (not all rendered together):
  Course Assistant  — AI advisor that reads your transcript + catalog
  Graduation Plan   — term-by-term path through remaining requirements
  Schedule Builder  — conflict-free sections from the live UNC catalog
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import streamlit as st

import agent
import catalog
import planner
import scheduler
from ui_style import apply_blue_theme

apply_blue_theme()
st.title("Planning")

SECTIONS = {
    "Course Assistant": "AI advisor that reads your transcript + catalog",
    "Graduation Plan": "Term-by-term path through your remaining requirements",
    "Schedule Builder": "Conflict-free sections from the live UNC catalog",
}


def _course_assistant() -> None:
    st.header("Course Assistant")
    st.caption(f"Your UNC Philosophy + CS degree advisor · {catalog.TERM_LABEL} catalog")

    _EXAMPLES = [
        "Have I finished the History of Philosophy requirement?",
        "What COMP courses are open this term, and when do they meet?",
        "Which courses should I take next toward the CS BA?",
        "Find a PHIL elective above 199 that meets Tue/Thu.",
    ]

    with st.expander("Example questions", expanded=False):
        for ex in _EXAMPLES:
            st.markdown(f"- {ex}")

    if not os.getenv("ANTHROPIC_API_KEY"):
        st.error("No `ANTHROPIC_API_KEY` found. Add it to your `.env` file.")
        return

    if "plan_api_msgs" not in st.session_state:
        st.session_state.plan_api_msgs = []
        st.session_state.plan_display = []

    c_chat, c_clear = st.columns([5, 1])
    if c_clear.button("Clear", key="clear_advisor"):
        st.session_state.plan_api_msgs = []
        st.session_state.plan_display = []
        st.rerun()

    chat_box = st.container(height=480)
    with chat_box:
        if not st.session_state.plan_display:
            st.caption("Ask about your degree requirements, course options, or schedule…")
        for msg in st.session_state.plan_display:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt := st.chat_input("Ask your advisor…", key="advisor_input"):
        st.session_state.plan_display.append({"role": "user", "content": prompt})
        st.session_state.plan_api_msgs.append({"role": "user", "content": prompt})

        with chat_box:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                try:
                    reply = st.write_stream(agent.stream_reply(st.session_state.plan_api_msgs))
                except Exception as e:
                    reply = f"Something went wrong: {e}"
                    st.error(reply)

        st.session_state.plan_display.append({"role": "assistant", "content": reply})


def _graduation_plan() -> None:
    st.header("Graduation Plan")
    st.caption("A term-by-term path through your remaining Philosophy + CS requirements.")

    c1, c2 = st.columns(2)
    start_term = c1.text_input("Start term", value="Spring 2027")
    credits_per_term = c2.number_input("Credits per term", min_value=9, max_value=21, value=15, step=1)
    c3, c4 = st.columns(2)
    include_summer = c3.checkbox("Use summer terms", value=False)
    graduate_by = c4.text_input("Graduate by (optional)", placeholder="e.g. Spring 2029")

    if st.button("Generate plan", type="primary", key="gen_plan"):
        with st.spinner("Your advisor is mapping out the terms…"):
            try:
                st.session_state.plan_result = planner.build(
                    start_term=start_term.strip() or "Spring 2027",
                    credits_per_term=int(credits_per_term),
                    include_summer=include_summer,
                    graduate_by=graduate_by.strip() or None,
                )
            except Exception as e:
                st.error(f"Couldn't generate a plan: {e}")

    result = st.session_state.get("plan_result")
    if result:
        plan = result["plan"]
        warnings = result["warnings"]
        terms = plan.get("terms", [])

        st.divider()
        if plan.get("summary"):
            st.markdown(plan["summary"])
        grad = plan.get("grad_term", "?")
        total = plan.get("total_credits") or sum(t.get("credits", 0) for t in terms)
        st.markdown(f"### Graduates: **{grad}** · {total} credits across {len(terms)} terms")

        if warnings:
            with st.expander(f"{len(warnings)} note(s) to review", expanded=True):
                for w in warnings:
                    st.markdown(f"- {w}")

        status_icon = {"completed": "[done]", "in_progress": "[in progress]", "planned": "•"}
        cumulative = 0
        for i, term in enumerate(terms):
            courses = term.get("courses", [])
            cumulative += term.get("credits", 0)
            is_history = bool(courses) and all(c.get("status", "planned") != "planned" for c in courses)
            with st.container(border=True):
                header = f"#### {term.get('term', '?')} — {term.get('credits', 0)} credits"
                if is_history:
                    header += "  ·  already done"
                st.markdown(header)
                for c in courses:
                    icon = status_icon.get(c.get("status", "planned"), "•")
                    st.write(
                        f"- {icon} **{c.get('code','')}** {c.get('title','')} "
                        f"({c.get('credits','?')} cr) · _{c.get('satisfies','')}_"
                    )
                st.caption(f"Cumulative: {cumulative} credits")
                planned_codes = [
                    c.get("code", "")
                    for c in courses
                    if c.get("status", "planned") == "planned" and c.get("code")
                ]
                if planned_codes and st.button("Build this term's schedule", key=f"sched_{i}"):
                    st.session_state.course_text = ", ".join(planned_codes)
                    st.session_state.jump_to_schedule = True
                    st.session_state.planning_section = "Schedule Builder"
                    st.rerun()

        st.caption(
            "Prerequisites are checked against the official UNC catalog, but future-term course "
            "*offerings* aren't known here — confirm availability in ConnectCarolina."
        )


def _render_week(sections: list[dict]) -> None:
    perday: dict[str, list] = defaultdict(list)
    untimed = []
    for s in sections:
        if not s["blocks"]:
            untimed.append(s)
            continue
        for pat in scheduler.meeting_patterns(s):
            for d in pat["days"]:
                perday[d].append((pat["start_min"], pat["end_min"], s))
    for d in scheduler.DAY_TOKENS:
        if d not in perday:
            continue
        st.markdown(f"**{scheduler.DAY_NAMES[d]}**")
        for smin, emin, s in sorted(perday[d]):
            badge = "[open]" if (s["seats_open"] or 0) > 0 else "[full]"
            st.write(
                f"- {scheduler.fmt_minutes(smin)}–{scheduler.fmt_minutes(emin)} · "
                f"**{s['course']}** {s['title']} · {s['instructor']} · {badge} {s['status']}"
            )
    if untimed:
        st.markdown("**Unscheduled / TBA**")
        for s in untimed:
            st.write(f"- **{s['course']}** {s['title']} · {s['instructor']}")


def _schedule_builder() -> None:
    st.header("Schedule Builder")
    st.caption(f"Conflict-free schedules from the {scheduler.catalog.TERM_LABEL} catalog.")

    if "course_text" not in st.session_state:
        st.session_state.course_text = ""

    # Apply pending advisor suggestion before the widget is created.
    pending_suggest = st.session_state.pop("_pending_suggest", None)
    if pending_suggest is not None:
        st.session_state.course_text = ", ".join(s["course"] for s in pending_suggest)
        st.session_state.suggest_reasons = pending_suggest

    if st.session_state.pop("jump_to_schedule", False):
        st.info("Courses pre-filled from your plan — hit **Build schedules** below.")

    st.text_input(
        "Courses to schedule (comma-separated)",
        key="course_text",
        placeholder="COMP 110, MATH 232, PHIL 210, STOR 155",
    )

    c1, c2 = st.columns([2, 1])
    focus_text = c1.text_input(
        "Advisor focus", value="my declared majors/minors",
        placeholder="e.g. my Biology major, or my remaining gen-ed requirements",
    )
    target_credits = c2.number_input("Target credits", min_value=3, max_value=21, value=15, step=1)

    if st.button("Suggest courses (advisor)", key="suggest"):
        with st.spinner("Asking your advisor…"):
            try:
                st.session_state["_pending_suggest"] = agent.suggest_courses(
                    focus_text.strip() or "my declared majors/minors", int(target_credits)
                )
            except Exception as e:
                st.error(f"Couldn't get suggestions: {e}")
            else:
                st.rerun()

    if reasons := st.session_state.get("suggest_reasons"):
        with st.expander("Why the advisor picked these", expanded=False):
            for s in reasons:
                st.markdown(f"- **{s['course']}** — {s['reason']}")

    o1, o2 = st.columns([1, 1])
    open_only = o1.checkbox("Open sections only", value=False)
    max_n = o2.slider("Max schedules", 1, 8, 5)

    if st.button("Build schedules", type="primary", key="build_sched"):
        courses = [c.strip() for c in re.split(r"[,\n]+", st.session_state.course_text) if c.strip()]
        if not courses:
            st.warning("Add at least one course (or use Suggest).")
        else:
            st.session_state.sched_result = scheduler.build_schedules(
                courses, open_only=open_only, max_results=max_n
            )

    sched_result = st.session_state.get("sched_result")
    if sched_result:
        st.divider()
        r = sched_result["reports"]
        if r["invalid"]:
            st.warning("Couldn't parse: " + ", ".join(r["invalid"]))
        if r["not_offered"]:
            st.warning("Not offered this term: " + ", ".join(r["not_offered"]))
        if r["no_sections"]:
            st.warning("No available sections (try unchecking 'open only'): " + ", ".join(r["no_sections"]))
        if r["lab_rec"]:
            st.info("These also have a lab/recitation to slot in manually: " + ", ".join(r["lab_rec"]))

        for idx, sched in enumerate(sched_result["schedules"]):
            with st.container(border=True):
                st.markdown(f"### Option {idx + 1} — {sched['total_credits']} credits")
                _render_week(sched["sections"])


_RENDERERS = {
    "Course Assistant": _course_assistant,
    "Graduation Plan": _graduation_plan,
    "Schedule Builder": _schedule_builder,
}

st.session_state.setdefault("planning_section", None)

if not st.session_state.planning_section:
    cols = st.columns(3)
    for i, (name, desc) in enumerate(SECTIONS.items()):
        with cols[i]:
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.caption(desc)
                if st.button("Open", key=f"open_section_{i}", use_container_width=True):
                    st.session_state.planning_section = name
                    st.rerun()
    st.stop()

if st.button("← All sections", key="back_to_planning"):
    st.session_state.planning_section = None
    st.rerun()

_RENDERERS[st.session_state.planning_section]()
