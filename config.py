"""Deployment/runtime configuration helpers."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("COURSE_MANAGER_DATA_DIR", BASE_DIR)).expanduser()

# Canvas due dates are UTC; students think in local time (UNC-Chapel Hill).
LOCAL_TZ = ZoneInfo("America/New_York")


def data_path(name: str) -> Path:
    """Path for persistent runtime data such as SQLite DBs and OAuth tokens."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


def env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def env_path(name: str, fallback_name: str) -> Path:
    value = env_text(name)
    if value:
        return Path(value).expanduser()
    return data_path(fallback_name)


def academic_records_text() -> str:
    """Load private academic records from env or a local runtime file."""
    value = env_text("ACADEMIC_RECORDS_TEXT")
    if value:
        return value
    path = env_path("ACADEMIC_RECORDS_PATH", "academic_records.txt")
    return path.read_text()
