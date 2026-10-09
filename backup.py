"""Backup/export helpers for the private Course Manager app."""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import assignments_store
import config
import grades_store
import study_notes_store


def _dump_db(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for row in tables:
            name = row["name"]
            records = conn.execute(f"SELECT * FROM {name}").fetchall()
            out[name] = [dict(r) for r in records]
    finally:
        conn.close()
    return out


def _write_json(zf: zipfile.ZipFile, name: str, data) -> None:
    zf.writestr(name, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def build_backup_zip() -> bytes:
    """Create a zip containing all user data and extracted course context."""
    buf = io.BytesIO()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    dbs = {
        "assignments": assignments_store.DB_PATH,
        "grades": grades_store.DB_PATH,
        "study_notes": study_notes_store.DB_PATH,
    }

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_json(
            zf,
            "manifest.json",
            {
                "created_at": ts,
                "data_dir": str(config.DATA_DIR),
                "databases": {name: str(path) for name, path in dbs.items()},
            },
        )
        for name, path in dbs.items():
            _write_json(zf, f"data/{name}.json", _dump_db(path))
            if path.exists():
                zf.write(path, f"sqlite/{path.name}")

        image_root = config.data_path("textbook_images")
        if image_root.exists():
            for path in image_root.rglob("*"):
                if path.is_file():
                    zf.write(path, f"uploads/textbook_images/{path.relative_to(image_root)}")

    return buf.getvalue()
