"""POST /v1/thesis/analyze, GET /v1/thesis/{id}[/report|/export] - packet 008 T16_thesis_formatter_M6.

Privacy default (D4/packet 008): this router never sends the uploaded document anywhere - analysis is 100%
local (ajp.thesis.pipeline calls only the local parser/checks/store). T17's similarity endpoints (added
separately) are the only ones that ever make an outbound request, and only for bounded search probes, never
the document text itself, and never at all unless the caller explicitly asks.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.thesis import pipeline, store
from ajp.thesis.docx_export import ExportError
from ajp.thesis.similarity.pipeline import default_adapters
from ajp.thesis.templates import TemplateError, list_templates
from ajp.api.schemas import ThesisTaskOut

ALL_SIMILARITY_SOURCES = list(default_adapters().keys()) + ["oa_fulltext", "copyleaks"]

router = APIRouter(prefix="/v1/thesis", tags=["thesis"])

NOTE_LOCAL_ONLY = ("Analysis runs entirely on this server, locally. The document is never sent to any "
                   "external service unless you separately and explicitly enable a similarity source that "
                   "requires it (see /v1/thesis/{id}/similarity).")


def _task_out(task: store.ThesisTask) -> ThesisTaskOut:
    return ThesisTaskOut(id=task.id, status=task.status, error=task.error, original_filename=task.original_filename,
                         source_format=task.source_format, template_name=task.template_name, report=task.report,
                         similarity=task.similarity, created_at=task.created_at)


@router.post("/analyze", response_model=ThesisTaskOut)
async def analyze(
    file: UploadFile = File(...),
    template: str = Query("rs_phd_generic"),
    conn: Connection = Depends(get_conn),
) -> ThesisTaskOut:
    content = await file.read()
    try:
        task = pipeline.analyze_upload(conn, store.DEFAULT_ROOT, filename=file.filename or "upload",
                                       content=content, template_name=template)
    except store.UploadRejected as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _task_out(task)


@router.get("/templates")
def templates() -> list:
    return list_templates()


@router.get("/{task_id}", response_model=ThesisTaskOut)
def get_task(task_id: UUID, conn: Connection = Depends(get_conn)) -> ThesisTaskOut:
    task = store.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such thesis task")
    return _task_out(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: UUID, conn: Connection = Depends(get_conn)) -> None:
    """T25 (packet 016): closes RC1's own documented gap - a real self-service delete, not just the bulk
    purge_older_than mechanism that already existed but had no per-document trigger."""
    if not store.delete_task(conn, store.DEFAULT_ROOT, task_id):
        raise HTTPException(status_code=404, detail="no such thesis task")


@router.get("/{task_id}/report")
def get_report(task_id: UUID, format: str = Query("json", pattern="^(json|pdf)$"),
               conn: Connection = Depends(get_conn)):
    task = store.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such thesis task")
    if format == "json":
        return _task_out(task)
    try:
        pdf_bytes = pipeline.render_task_report_pdf(conn, store.DEFAULT_ROOT, task_id)
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=thesis-report-{task_id}.pdf"})


@router.get("/similarity/sources")
def similarity_sources() -> list:
    """Every source id the UI can offer as an enable/disable toggle - packet 008's own D4 disclaimer text is
    shown by the UI alongside these, never by this endpoint (this is just the toggle list)."""
    return ALL_SIMILARITY_SOURCES


@router.post("/{task_id}/similarity")
def run_similarity(
    task_id: UUID,
    sources: Optional[List[str]] = Query(None, description="omit for all sources; pass to enable only these"),
    copyleaks_consent: bool = Query(False, description="explicit per-run consent - Copyleaks is never called without it"),
    conn: Connection = Depends(get_conn),
):
    try:
        report = pipeline.run_similarity_for_task(conn, store.DEFAULT_ROOT, task_id, enabled_sources=sources,
                                                  copyleaks_consent=copyleaks_consent)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return pipeline.similarity_report_to_dict(report)


@router.get("/{task_id}/similarity")
def get_similarity(task_id: UUID, conn: Connection = Depends(get_conn)):
    task = store.get_task(conn, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such thesis task")
    if task.similarity is None:
        raise HTTPException(status_code=404, detail="similarity has not been run for this task yet")
    return task.similarity


@router.get("/{task_id}/export")
def export_docx(task_id: UUID, conn: Connection = Depends(get_conn)):
    try:
        docx_bytes = pipeline.export_task_docx(conn, store.DEFAULT_ROOT, task_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ExportError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return Response(content=docx_bytes, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename=normalized-{task_id}.docx"})
