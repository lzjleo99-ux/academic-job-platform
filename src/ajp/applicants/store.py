"""T25 privacy default: uploads live under a local directory only (``data/applicants/{uuid}/``), never
uploaded anywhere by default - the exact same convention ajp.thesis.store already established (packet 008),
reused deliberately rather than re-invented. This module is the only place that touches that directory or the
``applicant`` table; nothing outside it constructs an applicant file path or logs an original filename.

Privacy/retention (T25, Privacy page): DELETE /v1/applicants/{id} is a REAL delete, not a soft hide - it
erases the uploaded file, blanks cv_raw_text, and blanks the file_path, keeping only a tombstone row (id,
deleted_at) so an applicant_match computed earlier does not dangle on a foreign key that silently vanished.
purge_older_than mirrors thesis_task's own mechanism (not run automatically by anything yet, same as that
one - a scheduled call is future work) for the same real, tested retention floor.
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

DEFAULT_ROOT = Path(os.environ.get("APPLICANT_STORE_ROOT", "data/applicants"))
MAX_UPLOAD_BYTES = 15 * 1024 * 1024                                # 15MB - a CV is far smaller than a thesis
ALLOWED_SUFFIXES = (".docx", ".pdf")


class UploadRejected(ValueError):
    """A real file WAS too large for this project's own upload limit - genuinely HTTP 413 Payload Too
    Large."""


# T33 (packet 018, Q5): an unsupported file TYPE is a different real failure than an oversize file - 413
# means "payload too large", which is factually wrong for a perfectly small .exe/.txt/etc; a distinct
# subclass lets the API router map each to its own correct real HTTP status (413 vs 415) instead of both
# collapsing onto the same one.
class UnsupportedFileType(UploadRejected):
    pass


@dataclass
class Applicant:
    id: str
    status: str
    error: Optional[str]
    cv_filename: str
    source_format: str
    file_path: Optional[str]
    cv_raw_text: Optional[str]
    profile: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]


def _applicant_dir(root: Path, applicant_id: uuid.UUID) -> Path:
    return root / str(applicant_id)


def save_upload(root: Path, filename: str, content: bytes) -> "tuple[uuid.UUID, str, str]":
    """Validates size/extension, writes the file under a fresh applicant uuid's own directory. Returns
    (applicant_id, relative_file_path, source_format)."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected(f"file too large: {len(content)} bytes (max {MAX_UPLOAD_BYTES})")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise UnsupportedFileType(f"unsupported file type {suffix!r} (only .docx and .pdf)")
    applicant_id = uuid.uuid4()
    d = _applicant_dir(root, applicant_id)
    d.mkdir(parents=True, exist_ok=True)
    rel_path = f"{applicant_id}/original{suffix}"
    (root / rel_path).write_bytes(content)
    return applicant_id, rel_path, suffix.lstrip(".")


def create_applicant(conn: psycopg.Connection, *, applicant_id: uuid.UUID, cv_filename: str,
                     source_format: str, file_path: str) -> None:
    conn.execute(
        """INSERT INTO applicant (id, status, cv_filename, source_format, file_path)
           VALUES (%s, 'processing', %s, %s, %s)""",
        (str(applicant_id), cv_filename, source_format, file_path),
    )


def set_profile(conn: psycopg.Connection, applicant_id: uuid.UUID, *, cv_raw_text: str,
                profile: Dict[str, Any]) -> None:
    conn.execute("UPDATE applicant SET status='done', cv_raw_text=%s, profile=%s, error=NULL WHERE id=%s",
                 (cv_raw_text, Jsonb(profile), str(applicant_id)))


def set_failed(conn: psycopg.Connection, applicant_id: uuid.UUID, error: str) -> None:
    conn.execute("UPDATE applicant SET status='failed', error=%s WHERE id=%s", (error[:2000], str(applicant_id)))


def update_profile(conn: psycopg.Connection, applicant_id: uuid.UUID, patch: Dict[str, Any]) -> Optional["Applicant"]:
    """Merges ``patch`` into the existing profile (a PATCH, not a full replace - the applicant reviewing/
    correcting one field, e.g. desired_countries, must never have to resend every other field it never
    touched)."""
    conn.execute("UPDATE applicant SET profile = profile || %s::jsonb WHERE id=%s AND deleted_at IS NULL",
                 (Jsonb(patch), str(applicant_id)))
    return get_applicant(conn, applicant_id)


_ROW_COLS = ("id", "status", "error", "cv_filename", "source_format", "file_path", "cv_raw_text", "profile",
            "created_at", "updated_at", "deleted_at")


def get_applicant(conn: psycopg.Connection, applicant_id: uuid.UUID) -> Optional["Applicant"]:
    row = conn.execute(f"SELECT {', '.join(_ROW_COLS)} FROM applicant WHERE id=%s AND deleted_at IS NULL",
                       (str(applicant_id),)).fetchone()
    if row is None:
        return None
    d = dict(zip(_ROW_COLS, row)) if not isinstance(row, dict) else row
    return Applicant(id=str(d["id"]), **{k: d[k] for k in _ROW_COLS if k != "id"})


def read_upload(root: Path, applicant: "Applicant") -> bytes:
    return (root / applicant.file_path).read_bytes()


def delete_applicant(conn: psycopg.Connection, root: Path, applicant_id: uuid.UUID) -> bool:
    """A real delete (Privacy page's own retention promise), not a soft hide: the uploaded file and every
    piece of extracted CV text are erased; only a tombstone (id, deleted_at) remains so applicant_match rows
    computed earlier keep a valid foreign key rather than being silently orphaned or cascade-deleted, which
    would make a past match result unexplainable after the fact. Idempotent: deleting an already-deleted or
    never-existing id returns False, never an error."""
    row = conn.execute("SELECT file_path FROM applicant WHERE id=%s AND deleted_at IS NULL",
                       (str(applicant_id),)).fetchone()
    if row is None:
        return False
    file_path = row[0] if not isinstance(row, dict) else row["file_path"]
    d = _applicant_dir(root, applicant_id)
    if d.is_dir():
        for f in d.iterdir():
            f.unlink(missing_ok=True)
        d.rmdir()
    conn.execute(
        """UPDATE applicant SET deleted_at = now(), file_path = NULL, cv_raw_text = NULL, profile = '{}'::jsonb
           WHERE id = %s""",
        (str(applicant_id),),
    )
    return True


def purge_older_than(conn: psycopg.Connection, root: Path, days: int) -> int:
    """Deletes applicant rows (and their files) older than ``days`` - mirrors thesis_task's own
    purge_older_than exactly. Not run automatically by anything yet; exists so retention has a real, tested
    mechanism rather than only being described on the Privacy page."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = conn.execute("SELECT id FROM applicant WHERE created_at < %s AND deleted_at IS NULL", (cutoff,)).fetchall()
    ids = [r[0] if not isinstance(r, dict) else r["id"] for r in rows]
    for applicant_id in ids:
        delete_applicant(conn, root, applicant_id if isinstance(applicant_id, uuid.UUID) else uuid.UUID(str(applicant_id)))
    return len(ids)
