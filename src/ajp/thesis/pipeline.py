"""T16/T17: ties store + parser + checks + templates + similarity together for the API layer. One function
per user-facing action; the API router (ajp/api/routers/thesis.py) is a thin HTTP wrapper around this
module, nothing more.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

import psycopg

from ajp.thesis import store
from ajp.thesis.checks import CheckReport, run_checks
from ajp.thesis.docx_export import ExportError, export_normalized_docx
from ajp.thesis.parser import ParseError, parse_document
from ajp.thesis.report_pdf import render_report_pdf
from ajp.thesis.similarity.formula import SimilarityReport
from ajp.thesis.similarity.pipeline import default_adapters, run_similarity
from ajp.thesis.similarity.sources.copyleaks import CopyleaksAdapter
from ajp.thesis.templates import Template, TemplateError, load_template


def report_to_dict(report: CheckReport) -> Dict[str, Any]:
    return {"template_name": report.template_name, "summary": report.summary,
            "findings": [asdict(f) for f in report.findings]}


def analyze_upload(conn: psycopg.Connection, root: Path, *, filename: str, content: bytes,
                   template_name: str = "rs_phd_generic") -> store.ThesisTask:
    tmpl = load_template(template_name)                       # raise TemplateError before writing anything
    task_id, rel_path, source_format = store.save_upload(root, filename, content)
    store.create_task(conn, task_id=task_id, original_filename=filename, source_format=source_format,
                      template_name=template_name, file_path=rel_path)
    try:
        doc = parse_document(root / rel_path)
        report = run_checks(doc, tmpl)
        store.set_report(conn, task_id, report_to_dict(report))
    except ParseError as e:
        store.set_failed(conn, task_id, str(e))
    return store.get_task(conn, task_id)


def render_task_report_pdf(conn: psycopg.Connection, root: Path, task_id: UUID) -> bytes:
    task = store.get_task(conn, task_id)
    if task is None or task.report is None:
        raise LookupError("no report available for this task")
    report = CheckReport(template_name=task.report["template_name"],
                         findings=[_finding_from_dict(f) for f in task.report["findings"]])
    return render_report_pdf(report, document_title=task.original_filename)


def export_task_docx(conn: psycopg.Connection, root: Path, task_id: UUID) -> bytes:
    task = store.get_task(conn, task_id)
    if task is None:
        raise LookupError("no such task")
    tmpl = load_template(task.template_name)
    doc = parse_document(root / task.file_path)
    return export_normalized_docx(doc, tmpl, cover_metadata={})


def _finding_from_dict(d: Dict[str, Any]):
    from ajp.thesis.checks import Finding
    return Finding(**d)


def similarity_report_to_dict(report: SimilarityReport) -> Dict[str, Any]:
    return asdict(report)


MAX_INTERNAL_CORPUS_DOCS = 5


def _internal_corpus_documents(conn: psycopg.Connection, root: Path, exclude_task_id: UUID) -> "List[tuple]":
    """'Previously analyzed docs per local workspace' (packet 008) - every OTHER successfully-parsed task in
    this same local thesis_task table, bounded to a handful so this stays fast and never a bulk re-read of
    an unbounded workspace."""
    rows = conn.execute(
        """SELECT id, original_filename, file_path FROM thesis_task
            WHERE id != %s AND status = 'done' ORDER BY created_at DESC LIMIT %s""",
        (str(exclude_task_id), MAX_INTERNAL_CORPUS_DOCS),
    ).fetchall()
    docs = []
    for row in rows:
        task_id, filename, file_path = row if not isinstance(row, dict) else (row["id"], row["original_filename"], row["file_path"])
        try:
            doc = parse_document(root / file_path)
        except ParseError:
            continue
        docs.append((filename, "", doc.full_text))
    return docs


def run_similarity_for_task(conn: psycopg.Connection, root: Path, task_id: UUID, *,
                            enabled_sources: Optional[Sequence[str]] = None,
                            copyleaks_consent: bool = False) -> SimilarityReport:
    task = store.get_task(conn, task_id)
    if task is None:
        raise LookupError("no such task")
    doc = parse_document(root / task.file_path)
    internal_docs = _internal_corpus_documents(conn, root, task_id)
    adapters = default_adapters(internal_corpus_documents=internal_docs)

    copyleaks_result = None
    copyleaks = CopyleaksAdapter()
    if enabled_sources is None or "copyleaks" in enabled_sources:
        raw_bytes = store.read_upload(root, task)
        copyleaks_result = copyleaks.run(str(task_id), raw_bytes, task.original_filename, consent=copyleaks_consent)

    report = run_similarity(doc, adapters, enabled_sources=enabled_sources, copyleaks_result=copyleaks_result)
    store.set_similarity(conn, task_id, similarity_report_to_dict(report))
    return report
