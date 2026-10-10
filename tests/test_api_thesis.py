"""T16 acceptance: 'API endpoints /v1/thesis/analyze (multipart) and /v1/thesis/{id}/report|export with task
status'. Exercises the real pipeline (parser + checks + store) through the HTTP layer, against the fixture
DB and a tmp_path thesis store root.
"""
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from ajp.api.app import create_app
from ajp.api.deps import get_conn
from ajp.thesis import store as thesis_store
from thesis_fixtures.build_fixtures import build_clean, build_defects


@pytest.fixture()
def client(conn, tmp_path, monkeypatch):
    conn.row_factory = dict_row
    monkeypatch.setattr(thesis_store, "DEFAULT_ROOT", tmp_path / "thesis_store")
    app = create_app()

    def _override_get_conn():
        yield conn

    app.dependency_overrides[get_conn] = _override_get_conn
    with TestClient(app) as c:
        yield c


def test_templates_endpoint_lists_the_seed_template(client):
    assert "rs_phd_generic" in client.get("/v1/thesis/templates").json()


def test_analyze_clean_fixture_end_to_end(client, tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze",
                        files={"file": ("clean.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["report"]["summary"]["fail"] == 0
    non_pass = [f for f in body["report"]["findings"] if f["severity"] not in ("pass",) and f["check"] != "page_numbering"]
    assert non_pass == []
    task_id = body["id"]

    got = client.get(f"/v1/thesis/{task_id}")
    assert got.status_code == 200 and got.json()["id"] == task_id


def test_analyze_defects_fixture_reports_failures(client, tmp_path):
    path, _ = build_defects(tmp_path / "defects.docx")
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze", files={"file": ("defects.docx", f)})
    body = r.json()
    assert body["report"]["summary"]["fail"] >= 5


def test_report_pdf_download(client, tmp_path):
    path = build_clean(tmp_path / "clean.docx")
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze", files={"file": ("clean.docx", f)})
    task_id = r.json()["id"]
    pdf = client.get(f"/v1/thesis/{task_id}/report", params={"format": "pdf"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")


def test_export_normalized_docx_download(client, tmp_path):
    path, _ = build_defects(tmp_path / "defects.docx")
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze", files={"file": ("defects.docx", f)})
    task_id = r.json()["id"]
    exported = client.get(f"/v1/thesis/{task_id}/export")
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"                          # a .docx is a zip archive


def test_analyze_rejects_unsupported_file_type(client):
    r = client.post("/v1/thesis/analyze", files={"file": ("thesis.txt", b"not a real thesis", "text/plain")})
    assert r.status_code == 413


def test_delete_erases_the_file_and_the_task_is_idempotent(client, tmp_path):
    """T25 (packet 016): closes RC1's own documented gap - a real self-service delete."""
    path = build_clean(tmp_path / "clean.docx")
    with open(path, "rb") as f:
        r = client.post("/v1/thesis/analyze", files={"file": ("clean.docx", f)})
    task_id = r.json()["id"]
    assert client.delete(f"/v1/thesis/{task_id}").status_code == 204
    assert client.get(f"/v1/thesis/{task_id}").status_code == 404
    assert client.delete(f"/v1/thesis/{task_id}").status_code == 404   # already gone, not an error


def test_delete_unknown_task_returns_404(client):
    assert client.delete("/v1/thesis/00000000-0000-0000-0000-000000000000").status_code == 404


def test_unknown_task_id_returns_404(client):
    assert client.get("/v1/thesis/00000000-0000-0000-0000-000000000000").status_code == 404


def test_no_filename_is_ever_logged(client, tmp_path, capsys):
    """packet 008: 'no filenames in logs beyond uuid'."""
    path = build_clean(tmp_path / "a-very-identifiable-real-name.docx")
    with open(path, "rb") as f:
        client.post("/v1/thesis/analyze", files={"file": ("a-very-identifiable-real-name.docx", f)})
    out = capsys.readouterr()
    assert "a-very-identifiable-real-name" not in out.out
    assert "a-very-identifiable-real-name" not in out.err
