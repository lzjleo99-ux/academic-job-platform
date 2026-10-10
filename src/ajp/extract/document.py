"""T31 (packet 019): plain, best-effort text extraction from a downloaded PDF/DOCX/legacy-DOC document link
found on a board listing (board.py's own 'document' extraction family - a linked konkurs announcement, not an
HTML detail page).

Deliberately NOT ajp.thesis.parser's ThesisDoc pipeline: that module's own heading/TOC/figure/abstract
detection is built for a well-formed academic thesis, not an arbitrary faculty announcement PDF, and this
module's own failure semantics are different - a scanned/unreadable document must never crash the crawl or
drop the row (D6: OCR is explicitly out of scope, never attempted; the row keeps its own original document
link either way, with an honest ``content_scanned``/failure flag instead of a fabricated body).
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import pdfplumber
from docx import Document as DocxDocument

# PM's own T31 threshold (packet 019): a PDF/DOC yielding fewer than 100 extractable characters across ALL
# its pages is treated as a scanned/image-only document, never as "genuinely empty real text".
SCANNED_MIN_CHARS = 100

# T34 (packet 018/020, PM-verified defect): a broken PDF ToUnicode CMap can produce a LONG string that still
# clears the bare character-count floor above while being mostly U+FFFD replacement-character garbage, not
# real text at all - a second, independent signal PM's own fix explicitly asks for: more than 5% of the
# extracted text being U+FFFD, or fewer than ~50 real alphabetic characters in it at all, is ALSO scanned.
_MIN_ALPHA_CHARS = 50
_MAX_FFFD_RATIO = 0.05


def _looks_scanned_or_garbled(text: str) -> bool:
    if len(text) < SCANNED_MIN_CHARS:
        return True
    fffd_ratio = text.count("�") / len(text)
    alpha_count = sum(1 for c in text if c.isalpha())
    return fffd_ratio > _MAX_FFFD_RATIO or alpha_count < _MIN_ALPHA_CHARS


@dataclass
class ExtractedDocument:
    text: str
    page_count: Optional[int]
    content_scanned: bool      # <100 extractable chars total (or pdfplumber found no text objects at all)
    extract_failed: bool       # could not even attempt to read the document (corrupt/unsupported/timed out)
    failure_reason: Optional[str] = None


def extract_pdf_text(path: Union[str, Path]) -> ExtractedDocument:
    try:
        with pdfplumber.open(str(path)) as pdf:
            page_texts = [(pg.extract_text() or "") for pg in pdf.pages]
            text = "\n".join(page_texts).strip()
            return ExtractedDocument(text=text, page_count=len(pdf.pages),
                                     content_scanned=_looks_scanned_or_garbled(text), extract_failed=False)
    except Exception as e:                                # noqa: BLE001 - any real corrupt/malformed PDF
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason=f"{type(e).__name__}: {e}")


def extract_docx_text(path: Union[str, Path]) -> ExtractedDocument:
    try:
        doc = DocxDocument(str(path))
        text = "\n".join(p.text for p in doc.paragraphs).strip()
        return ExtractedDocument(text=text, page_count=None,
                                 content_scanned=_looks_scanned_or_garbled(text), extract_failed=False)
    except Exception as e:                                 # noqa: BLE001 - incl. PackageNotFoundError (a
        # corrupt file or a non-DOCX file wearing a .docx extension - both real, both must never crash the crawl)
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason=f"{type(e).__name__}: {e}")


def extract_legacy_doc_text(path: Union[str, Path]) -> ExtractedDocument:
    """Legacy pre-2007 binary .doc - python-docx cannot open it at all, and this project never adds a whole
    extra dependency (antiword/LibreOffice/odfpy) just for the 2 known real rows PM's own recon found. macOS's
    own `textutil` (a real system tool already present on every dev/prod machine this runs on today, per
    PM's own instruction) is used ONLY if it exists on PATH; anywhere else this honestly reports
    extract_failed rather than guessing or attempting OCR (D6, explicitly out of scope)."""
    textutil = shutil.which("textutil")
    if not textutil:
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason="legacy .doc format and macOS textutil is not available on this host")
    # Found live testing this exact function: a missing input file is NOT a non-zero exit from textutil -
    # it prints "Error reading ... doesn't exist." to its own stdout and exits 0, which would otherwise be
    # silently misread as a real (if very short) extraction, or worse as content_scanned=True. Checked
    # explicitly up front rather than trusted to the subprocess's own exit code, which pypdf/pdfplumber and
    # python-docx (both raise a real exception on a missing file, already covered by check=True there) do
    # not share with this specific external tool.
    if not Path(path).is_file():
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason=f"file not found: {path}")
    try:
        result = subprocess.run([textutil, "-convert", "txt", "-stdout", str(path)],
                                capture_output=True, timeout=30, check=True)
        text = result.stdout.decode("utf-8", "replace").strip()
        return ExtractedDocument(text=text, page_count=None,
                                 content_scanned=_looks_scanned_or_garbled(text), extract_failed=False)
    except Exception as e:                                 # noqa: BLE001 - subprocess failure/timeout/non-zero exit
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason=f"{type(e).__name__}: {e}")


_EXTRACTORS = {".pdf": extract_pdf_text, ".docx": extract_docx_text, ".doc": extract_legacy_doc_text}


def extract_document_text(path: Union[str, Path], suffix: str) -> ExtractedDocument:
    fn = _EXTRACTORS.get(suffix.lower())
    if fn is None:
        return ExtractedDocument(text="", page_count=None, content_scanned=False, extract_failed=True,
                                 failure_reason=f"unsupported document type: {suffix!r}")
    return fn(path)
