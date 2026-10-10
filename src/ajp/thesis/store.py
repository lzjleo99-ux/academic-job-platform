"""T16/T17 privacy default: uploads live under a local directory only (``data/thesis/{uuid}/``), never
uploaded anywhere by default (packet 008). This module is the only place that touches that directory or the
``thesis_task`` table; nothing outside it constructs a thesis file path or logs an original filename - every
log line elsewhere in this package refers to a task only by its uuid.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg
from psycopg.types.json import Jsonb

DEFAULT_ROOT = Path(os.environ.get("THESIS_STORE_ROOT", "data/thesis"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024                                # 25MB - a PhD dissertation, generously
ALLOWED_SUFFIXES = (".docx", ".pdf")


class UploadRejected(ValueError):
    pass


@dataclass
class ThesisTask:
    id: str
    status: str
    error: Optional[str]
    original_filename: str
    source_format: str
    template_name: str
    file_path: str
    report: Optional[Dict[str, Any]]
    similarity: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


def _task_dir(root: Path, task_id: uuid.UUID) -> Path:
    return root / str(task_id)


def save_upload(root: Path, filename: str, content: bytes) -> "tuple[uuid.UUID, str, str]":
    """Validates size/extension, writes the file under a fresh task uuid's own directory. Returns
    (task_id, relative_file_path, source_format)."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected(f"file too large: {len(content)} bytes (max {MAX_UPLOAD_BYTES})")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UploadRejected(f"unsupported file type {suffix!r} (only .docx and .pdf)")
    task_id = uuid.uuid4()
    d = _task_dir(root, task_id)
    d.mkdir(parents=True, exist_ok=True)
    rel_path = f"{task_id}/original{suffix}"
    (root / rel_path).write_bytes(content)
    return task_id, rel_path, suffix.lstrip(".")


def create_task(conn: psycopg.Connection, *, task_id: uuid.UUID, original_filename: str, source_format: str,
                template_name: str, file_path: str) -> None:
    conn.execute(
        """INSERT INTO thesis_task (id, status, original_filename, source_format, template_name, file_path)
           VALUES (%s, 'processing', %s, %s, %s, %s)""",
        (str(task_id), original_filename, source_format, template_name, file_path),
    )


def set_report(conn: psycopg.Connection, task_id: uuid.UUID, report: Dict[str, Any]) -> None:
    conn.execute("UPDATE thesis_task SET status='done', report=%s, error=NULL WHERE id=%s",
                 (Jsonb(report), str(task_id)))


def set_failed(conn: psycopg.Connection, task_id: uuid.UUID, error: str) -> None:
    conn.execute("UPDATE thesis_task SET status='failed', error=%s WHERE id=%s", (error[:2000], str(task_id)))


def set_similarity(conn: psycopg.Connection, task_id: uuid.UUID, similarity: Dict[str, Any]) -> None:
    conn.execute("UPDATE thesis_task SET similarity=%s WHERE id=%s", (Jsonb(similarity), str(task_id)))


_ROW_COLS = ("id", "status", "error", "original_filename", "source_format", "template_name", "file_path",
            "report", "similarity", "created_at", "updated_at")


def get_task(conn: psycopg.Connection, task_id: uuid.UUID) -> Optional[ThesisTask]:
    row = conn.execute(f"SELECT {', '.join(_ROW_COLS)} FROM thesis_task WHERE id=%s", (str(task_id),)).fetchone()
    if row is None:
        return None
    d = dict(zip(_ROW_COLS, row)) if not isinstance(row, dict) else row
    return ThesisTask(id=str(d["id"]), **{k: d[k] for k in _ROW_COLS if k != "id"})


def read_upload(root: Path, task: ThesisTask) -> bytes:
    return (root / task.file_path).read_bytes()


def delete_task(conn: psycopg.Connection, root: Path, task_id: uuid.UUID) -> bool:
    """T25 (packet 016): closes RC1's own documented gap ('no self-service delete button in this interface')
    - a real delete, mirroring ajp.applicants.store.delete_applicant's own pattern exactly (built for T25,
    reused here rather than inventing a second delete convention): the uploaded file is erased and the row
    itself is removed outright (unlike applicant's tombstone-row convention, a thesis task has no downstream
    foreign key anything else could dangle on, so there is nothing a tombstone would need to protect).
    Idempotent: deleting an already-deleted or never-existing id returns False, never an error."""
    row = conn.execute("SELECT file_path FROM thesis_task WHERE id=%s", (str(task_id),)).fetchone()
    if row is None:
        return False
    d = _task_dir(root, task_id)
    if d.is_dir():
        for f in d.iterdir():
            f.unlink(missing_ok=True)
        d.rmdir()
    conn.execute("DELETE FROM thesis_task WHERE id=%s", (str(task_id),))
    return True


def purge_older_than(conn: psycopg.Connection, root: Path, days: int) -> int:
    """Deletes task rows (and their files) older than ``days``. Not run automatically by anything yet - a
    scheduled call is future work (T21's scheduler); exists now so retention has a real, tested mechanism
    rather than only being described in prose."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = conn.execute("SELECT id FROM thesis_task WHERE created_at < %s", (cutoff,)).fetchall()
    ids = [r[0] if not isinstance(r, dict) else r["id"] for r in rows]
    for task_id in ids:
        d = _task_dir(root, task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id)))
        if d.is_dir():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()
    if ids:
        conn.execute("DELETE FROM thesis_task WHERE id = ANY(%s)", ([str(i) for i in ids],))
    return len(ids)
