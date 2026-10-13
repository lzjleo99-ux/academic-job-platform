"""POST /v1/applicants, GET/PATCH/DELETE /v1/applicants/{id}, POST/GET /v1/applicants/{id}/matches -
packet 015/016 T25.

Privacy default (same standing rule as thesis, packet 008/D4): a CV is analysed 100% locally - this router
never sends the uploaded document, its extracted text, or the structured profile anywhere else. DELETE is a
real delete (ajp.applicants.store.delete_applicant erases the file and the extracted text, keeping only a
tombstone row), not a soft hide - see the Privacy page for the retention statement this endpoint backs.

T49 (2026-10-13): every write here is PUBLIC self-service by design, so it is bounded by the inbound rate
limiter (ajp.api.ratelimit) rather than a token a browser cannot hold. Volume is the control that fits;
identity is not.
"""
from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from psycopg import Connection

from ajp.api.deps import get_conn
from ajp.api.ratelimit import rate_limit
from ajp.api.schemas import ApplicantMatchesResponse, ApplicantMatchOut, ApplicantOut
from ajp.applicants import pipeline, store
from ajp.applicants.matching import MATCH_WEIGHTS

router = APIRouter(prefix="/v1/applicants", tags=["applicants"])


@router.get("/matching/weights")
def matching_weights() -> Dict[str, int]:
    """The exact, real weights ajp.applicants.matching.score_listing uses - the methodology page fetches
    and renders this directly rather than restating the numbers by hand, so the two can never silently drift
    apart (the same D4-style transparency standard already applied to the thesis similarity formula)."""
    return MATCH_WEIGHTS


def _applicant_out(a: store.Applicant) -> ApplicantOut:
    return ApplicantOut(id=a.id, status=a.status, error=a.error, cv_filename=a.cv_filename,
                        source_format=a.source_format, profile=a.profile, created_at=a.created_at)


@router.post("", response_model=ApplicantOut)
async def upload(request: Request, file: UploadFile = File(...), conn: Connection = Depends(get_conn),
                 _rl: None = Depends(rate_limit("upload"))) -> ApplicantOut:
    content = await file.read()
    try:
        applicant = pipeline.upload_cv(conn, store.DEFAULT_ROOT, filename=file.filename or "cv", content=content)
    # T33 (packet 018, Q5): UnsupportedFileType is checked FIRST - it is a subclass of UploadRejected, so
    # checking the base class first would silently swallow it into the wrong status code again.
    except store.UnsupportedFileType as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    except store.UploadRejected as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    return _applicant_out(applicant)


@router.get("/{applicant_id}", response_model=ApplicantOut)
def get_applicant(applicant_id: UUID, conn: Connection = Depends(get_conn)) -> ApplicantOut:
    applicant = store.get_applicant(conn, applicant_id)
    if applicant is None:
        raise HTTPException(status_code=404, detail="no such applicant")
    return _applicant_out(applicant)


@router.patch("/{applicant_id}", response_model=ApplicantOut)
def patch_profile(applicant_id: UUID, patch: Dict[str, Any], request: Request,
                  conn: Connection = Depends(get_conn), _rl: None = Depends(rate_limit("write"))) -> ApplicantOut:
    """A manual correction to the extracted profile - merges ``patch`` into the existing profile (never a
    full replace), so reviewing/fixing one field never requires resending every other one."""
    applicant = store.update_profile(conn, applicant_id, patch)
    if applicant is None:
        raise HTTPException(status_code=404, detail="no such applicant")
    return _applicant_out(applicant)


@router.delete("/{applicant_id}", status_code=204)
def delete_applicant(applicant_id: UUID, request: Request, conn: Connection = Depends(get_conn),
                     _rl: None = Depends(rate_limit("write"))) -> None:
    deleted = store.delete_applicant(conn, store.DEFAULT_ROOT, applicant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="no such applicant")


# T33 (packet 018, Q4): a real applicant's own matches against a 4.7k-row DB must never be dumped unbounded
# on one response - default top-100 by score, real total count alongside the page, explicit limit/offset.
_DEFAULT_MATCHES_LIMIT = 100
_MAX_MATCHES_LIMIT = 500


@router.post("/{applicant_id}/matches", response_model=ApplicantMatchesResponse)
def compute_matches(request: Request, applicant_id: UUID,
                    limit: int = Query(_DEFAULT_MATCHES_LIMIT, ge=1, le=_MAX_MATCHES_LIMIT),
                    offset: int = Query(0, ge=0), conn: Connection = Depends(get_conn),
                    _rl: None = Depends(rate_limit("compute"))) -> ApplicantMatchesResponse:
    results = pipeline.compute_matches(conn, applicant_id)
    if results is None:
        raise HTTPException(status_code=404, detail="no such applicant")
    fetched = pipeline.get_matches(conn, applicant_id, limit=limit, offset=offset)
    return ApplicantMatchesResponse(total=fetched["total"], results=[ApplicantMatchOut(**row) for row in fetched["results"]])


@router.get("/{applicant_id}/matches", response_model=ApplicantMatchesResponse)
def get_matches(applicant_id: UUID, limit: int = Query(_DEFAULT_MATCHES_LIMIT, ge=1, le=_MAX_MATCHES_LIMIT),
                offset: int = Query(0, ge=0), conn: Connection = Depends(get_conn)) -> ApplicantMatchesResponse:
    if store.get_applicant(conn, applicant_id) is None:
        raise HTTPException(status_code=404, detail="no such applicant")
    fetched = pipeline.get_matches(conn, applicant_id, limit=limit, offset=offset)
    return ApplicantMatchesResponse(total=fetched["total"], results=[ApplicantMatchOut(**row) for row in fetched["results"]])
