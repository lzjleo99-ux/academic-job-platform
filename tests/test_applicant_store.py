"""T25: ajp.applicants.store - local-only file storage, mirrors ajp.thesis.store's own convention. Direct
unit tests of the store layer (the API suite exercises it again through the HTTP layer, but a real delete's
own file-erasure guarantee is worth a direct, non-HTTP test too)."""
import uuid

import pytest

from ajp.applicants import store


def test_save_upload_rejects_an_unsupported_file_type(tmp_path):
    with pytest.raises(store.UploadRejected):
        store.save_upload(tmp_path, "cv.txt", b"plain text is not accepted")


def test_save_upload_rejects_an_oversized_file(tmp_path):
    with pytest.raises(store.UploadRejected):
        store.save_upload(tmp_path, "cv.pdf", b"x" * (store.MAX_UPLOAD_BYTES + 1))


def test_save_upload_writes_the_file_under_its_own_uuid_directory(tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"%PDF- fake")
    assert fmt == "pdf"
    assert (tmp_path / rel_path).read_bytes() == b"%PDF- fake"
    assert rel_path.startswith(str(applicant_id))


def test_create_get_and_set_profile_round_trip(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.docx", b"fake docx bytes")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.docx", source_format=fmt,
                           file_path=rel_path)
    a = store.get_applicant(conn, applicant_id)
    assert a.status == "processing" and a.cv_filename == "cv.docx" and a.profile == {}

    store.set_profile(conn, applicant_id, cv_raw_text="full text here", profile={"skills": ["Python"]})
    a2 = store.get_applicant(conn, applicant_id)
    assert a2.status == "done" and a2.cv_raw_text == "full text here" and a2.profile == {"skills": ["Python"]}


def test_set_failed_records_the_error_and_status(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"corrupt")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.pdf", source_format=fmt,
                           file_path=rel_path)
    store.set_failed(conn, applicant_id, "could not parse PDF")
    a = store.get_applicant(conn, applicant_id)
    assert a.status == "failed" and a.error == "could not parse PDF"


def test_update_profile_merges_never_replaces(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.docx", b"x")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.docx", source_format=fmt, file_path=rel_path)
    store.set_profile(conn, applicant_id, cv_raw_text="t", profile={"skills": ["Python"], "degree": {"v": "phd"}})
    a = store.update_profile(conn, applicant_id, {"skills": ["Python", "R"]})
    assert a.profile == {"skills": ["Python", "R"], "degree": {"v": "phd"}}    # degree untouched


def test_get_applicant_returns_none_for_an_unknown_id(conn):
    assert store.get_applicant(conn, uuid.uuid4()) is None


def test_delete_applicant_erases_the_file_and_cv_text_keeping_only_a_tombstone(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"real cv bytes")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.pdf", source_format=fmt, file_path=rel_path)
    store.set_profile(conn, applicant_id, cv_raw_text="sensitive cv text", profile={"skills": ["Python"]})

    assert store.delete_applicant(conn, tmp_path, applicant_id) is True
    assert not (tmp_path / rel_path).exists()
    assert store.get_applicant(conn, applicant_id) is None            # never returned as a live applicant again
    row = conn.execute("SELECT file_path, cv_raw_text, profile, deleted_at FROM applicant WHERE id=%s",
                       (str(applicant_id),)).fetchone()
    assert row[0] is None and row[1] is None and row[2] == {} and row[3] is not None


def test_deleting_an_already_deleted_applicant_is_idempotent_not_an_error(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"x")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.pdf", source_format=fmt, file_path=rel_path)
    assert store.delete_applicant(conn, tmp_path, applicant_id) is True
    assert store.delete_applicant(conn, tmp_path, applicant_id) is False


def test_deleting_a_never_existed_id_returns_false_never_raises(conn, tmp_path):
    assert store.delete_applicant(conn, tmp_path, uuid.uuid4()) is False


def test_purge_older_than_deletes_only_rows_past_the_cutoff(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"x")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.pdf", source_format=fmt, file_path=rel_path)
    conn.execute("UPDATE applicant SET created_at = now() - interval '400 days' WHERE id=%s", (str(applicant_id),))
    assert store.purge_older_than(conn, tmp_path, days=365) == 1
    assert store.get_applicant(conn, applicant_id) is None


def test_purge_older_than_leaves_recent_rows_alone(conn, tmp_path):
    applicant_id, rel_path, fmt = store.save_upload(tmp_path, "cv.pdf", b"x")
    store.create_applicant(conn, applicant_id=applicant_id, cv_filename="cv.pdf", source_format=fmt, file_path=rel_path)
    assert store.purge_older_than(conn, tmp_path, days=365) == 0
    assert store.get_applicant(conn, applicant_id) is not None
