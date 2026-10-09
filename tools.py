"""Claude tool definitions + dispatch for the course assistant.

The student's transcript and degree requirements live in the system prompt
(see agent.py). These tools cover the thing that's too big to inline: the
124-subject Fall 2026 course catalog.
"""
from __future__ import annotations

import json

import catalog

TOOLS = [
    {
        "name": "search_catalog",
        "description": (
            f"Search the UNC {catalog.TERM_LABEL} course catalog for actual sections being "
            "offered. Call this whenever the student asks what's available, when a course "
            "meets, who teaches it, whether seats are open, or which courses could satisfy a "
            "remaining requirement. All parameters are optional and combined with AND; provide "
            "the narrowest set that answers the question. Returns matching sections with meeting "
            "times, instructor, seats open, and course attributes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Subject code, e.g. 'PHIL', 'COMP', 'STOR'. Use list_subjects if unsure.",
                },
                "catalog_nbr": {
                    "type": "string",
                    "description": "Course-number prefix, e.g. '210' for COMP 210, or '2' for all 200-level.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Case-insensitive words to match in the course title/topic, e.g. 'logic'.",
                },
                "days": {
                    "type": "string",
                    "description": "Meeting-day code substring, e.g. 'TuTh', 'MW', 'MWF'.",
                },
                "open_only": {
                    "type": "boolean",
                    "description": "If true, only return sections that currently have seats available.",
                },
                "attribute": {
                    "type": "string",
                    "description": "A course-attribute / gen-ed code that must appear in the section's attributes (e.g. 'GE').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max sections to return (default 40).",
                },
            },
        },
    },
    {
        "name": "list_subjects",
        "description": (
            f"List every subject code that has {catalog.TERM_LABEL} offerings. Call this when you "
            "need to confirm a subject code exists before searching, or to map a department name "
            "to its code."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

_SEARCH_KEYS = ("subject", "catalog_nbr", "keyword", "days", "open_only", "attribute", "limit")


def run_tool(name: str, tool_input: dict) -> str:
    """Execute a tool call and return a string result for the model."""
    try:
        if name == "search_catalog":
            kwargs = {k: tool_input[k] for k in _SEARCH_KEYS if k in tool_input}
            results = catalog.search(**kwargs)
            if not results:
                return "No matching sections found for those criteria."
            return json.dumps({"count": len(results), "sections": results}, indent=2)
        if name == "list_subjects":
            return ", ".join(catalog.available_subjects())
        return f"Unknown tool: {name}"
    except Exception as e:  # surface tool errors to the model rather than crashing
        return f"Tool error in {name}: {e}"
