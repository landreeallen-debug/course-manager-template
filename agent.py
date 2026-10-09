"""The Claude-powered advising agent: system prompt + streaming tool loop.

The student's transcript and the official degree requirements are loaded into a
cached system prompt (always relevant, small). The course catalog is reached
through tools (see tools.py) so only the sections Claude asks for enter context.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import catalog
import config
import tools as tools_mod

load_dotenv()

MODEL = "claude-opus-4-8"
MAX_TOKENS = 16000

STUDENT_NAME = os.getenv("STUDENT_NAME", "the student")
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "").strip()
_SCHOOL_SUFFIX = f" at {SCHOOL_NAME}" if SCHOOL_NAME else ""

PERSONA = f"""You are {STUDENT_NAME}'s personal academic advisor{_SCHOOL_SUFFIX}.

The student's full transcript and official degree requirements are given below —
treat them as the source of truth for their majors/minors and gen-ed, and for what
is completed, in progress, and still needed. Don't assume specific majors beyond
what those records state.

How to help:
- Be concrete and accurate. Distinguish clearly between requirements that are
  completed, currently in progress, and not yet started. Never count an in-progress
  course as finished.
- When the student asks what to take, which sections fit, who teaches a course, or
  whether something is open, use the search_catalog tool against the live
  {catalog.TERM_LABEL} catalog rather than guessing. Cite specific section numbers,
  meeting times, and seat availability.
- When recommending courses, tie each one back to a specific requirement it satisfies.
- If a course or subject isn't offered this term, say so plainly.
- Keep answers focused and skimmable. Use short headers or bullet lists for
  multi-part answers; don't pad."""


def build_system() -> list[dict]:
    """System prompt text blocks: persona + today's date, then the cached records."""
    today = dt.datetime.now(config.LOCAL_TZ)
    header = (
        PERSONA
        + f"\n\nToday's date is {today:%A, %B %d, %Y}. The local timezone is "
        f"{config.LOCAL_TZ.key}. Use it to resolve relative dates like 'tomorrow' or 'next Friday'."
    )
    records = config.academic_records_text()
    return [
        {"type": "text", "text": header},
        {
            "type": "text",
            "text": "STUDENT ACADEMIC RECORDS AND DEGREE REQUIREMENTS\n\n" + records,
            "cache_control": {"type": "ephemeral"},
        },
    ]


_CLIENT: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Put it in a .env file in this folder."
            )
        _CLIENT = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _CLIENT


def stream_reply(api_messages: list[dict]):
    """Stream the assistant's reply, running the tool loop as needed.

    Yields text chunks suitable for Streamlit's st.write_stream. `api_messages`
    is mutated in place: assistant turns (including thinking/tool_use blocks) and
    tool results are appended so the conversation stays valid across turns.
    """
    client = _client()
    system = build_system()

    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools_mod.TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            messages=api_messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
            final = stream.get_final_message()

        # Preserve full content (text + thinking + tool_use) for the next turn.
        api_messages.append({"role": "assistant", "content": final.content})

        if final.stop_reason != "tool_use":
            return

        tool_results = []
        for block in final.content:
            if block.type == "tool_use":
                yield f"\n\n*Using {block.name}...*\n\n"
                result = tools_mod.run_tool(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        api_messages.append({"role": "user", "content": tool_results})


_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "courses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course": {"type": "string", "description": "Course code, e.g. 'COMP 110'."},
                    "reason": {"type": "string", "description": "One line: the requirement it satisfies."},
                },
                "required": ["course", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["courses"],
    "additionalProperties": False,
}


def suggest_courses(focus: str = "my declared majors/minors", target_credits: int = 15) -> list[dict]:
    """Ask the advisor for a candidate course list toward remaining requirements.

    Returns a list of {"course": "COMP 110", "reason": "..."}. The caller validates
    each against the live catalog (offered / has sections).
    """
    client = _client()
    prompt = (
        f"Recommend specific UNC courses I should take next ({focus}) toward my remaining "
        f"degree requirements, aiming for about {target_credits} credits total. Do NOT include "
        "any course I've already completed or that is currently in progress. For each course, "
        "give its code (e.g. 'COMP 110') and a one-line reason naming the requirement it "
        "satisfies. Prefer foundational/prerequisite courses where they unlock later ones."
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=build_system(),
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _SUGGEST_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        return json.loads(text).get("courses", [])
    except json.JSONDecodeError:
        return []


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "grad_term": {"type": "string", "description": "Projected graduation term, e.g. 'Spring 2029'."},
        "total_credits": {"type": "number"},
        "summary": {"type": "string", "description": "2-3 sentence overview of the plan."},
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "e.g. 'Spring 2027'."},
                    "credits": {"type": "number"},
                    "courses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string", "description": "e.g. 'COMP 210'."},
                                "title": {"type": "string"},
                                "credits": {"type": "number"},
                                "satisfies": {"type": "string", "description": "Requirement it fulfills."},
                                "status": {
                                    "type": "string",
                                    "enum": ["completed", "in_progress", "planned"],
                                    "description": "'completed'/'in_progress' for courses already on my record; 'planned' for future ones.",
                                },
                            },
                            "required": ["code", "title", "credits", "satisfies", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["term", "credits", "courses"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["grad_term", "total_credits", "summary", "terms"],
    "additionalProperties": False,
}


def graduation_plan(
    start_term: str = "Spring 2027",
    credits_per_term: int = 15,
    include_summer: bool = False,
    graduate_by: str | None = None,
) -> dict:
    """Produce a term-by-term plan to finish both degrees (structured output)."""
    import planner  # function-level import avoids a module-load cycle

    client = _client()
    done = sorted(planner.completed_courses())
    done_str = ", ".join(done) if done else "(none parsed)"
    summer = "Include summer terms when they help." if include_summer else "Do not include summer terms."
    target = f" Aim to graduate by {graduate_by}." if graduate_by else ""
    prompt = (
        f"Build a complete term-by-term degree plan for ALL of my declared majors/minors, with the "
        f"timeline BEGINNING at {start_term}.\n\n"
        f"Courses I have already completed or have in progress — treat ALL of these as DONE and "
        f"NEVER schedule them again: {done_str}. My transcript below also lists transfer and "
        "AP/test credit; count those as done too.\n\n"
        "Rules:\n"
        f"- Start the timeline at {start_term}. If that term is in the past or matches terms I've "
        "already attended, show those terms populated with the courses I actually took / am taking, "
        "marking each course's status 'completed' or 'in_progress'. Put undated transfer/AP credit "
        "in one early entry titled 'Transfer & AP credit'.\n"
        "- For every later term, include ONLY requirements I still need, with status 'planned'.\n"
        "- Respect prerequisites and each major/minor's required sequencing as stated in the records "
        "below (or known to you for this school).\n"
        f"- Keep each PLANNED term near {credits_per_term} credits. {summer}{target}\n"
        "- For each course give code, title, credit hours, the requirement it satisfies, and status. "
        "End when all requirements are met (≈120 total hours plus remaining gen-ed). Provide the "
        "graduation term, total credits, and a short summary."
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=build_system(),
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": _PLAN_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"grad_term": "", "total_credits": 0, "summary": "", "terms": []}


_SYLLABUS_SCHEMA = {
    "type": "object",
    "properties": {
        "course": {"type": "string", "description": "Course code if identifiable, e.g. 'COMP 110'; else ''."},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title, e.g. 'Problem Set 3' or 'Midterm 1'."},
                    "type": {
                        "type": "string",
                        "enum": ["assignment", "exam", "quiz", "project", "reading", "other"],
                    },
                    "due_date": {
                        "type": "string",
                        "description": "ISO 8601 date (or datetime) if a concrete one is given; else ''.",
                    },
                    "weight_pct": {
                        "type": "string",
                        "description": "Percent of final grade as a number string, e.g. '15'; else ''.",
                    },
                },
                "required": ["title", "type", "due_date", "weight_pct"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["course", "assignments"],
    "additionalProperties": False,
}

_SYLLABUS_INSTRUCTION = (
    "Extract every graded assignment, exam, quiz, and project from this syllabus. For each, give "
    "a short title, a type, the due date as an ISO 8601 date if a concrete one is stated (else ''), "
    "and its percent weight of the final grade if stated (else ''). Also identify the course code "
    "if present. Ignore policies, office hours, and ungraded readings."
)


def extract_syllabus(pdf_bytes: bytes, course_hint: str = "") -> dict:
    """Extract assignments/exams from a syllabus PDF. Returns {course, assignments[]}."""
    client = _client()
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    hint = f" The course is {course_hint}." if course_hint else ""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    },
                    {"type": "text", "text": _SYLLABUS_INSTRUCTION + hint},
                ],
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": _SYLLABUS_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"course": course_hint, "assignments": []}
    if course_hint and not data.get("course"):
        data["course"] = course_hint
    return data
