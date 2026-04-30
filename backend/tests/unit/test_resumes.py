"""Unit tests for ``handlers/resumes.py``.

Mocks the pymysql cursor, ``get_user_id``, and the boto3 S3 client so the
presigned-URL flow can be exercised without AWS credentials.

Covers:
  * route dispatch (list / create / detail / update / delete / upload-url)
  * first-resume-auto-master + clearing other masters on toggle
  * presigned PUT-URL signing + key layout
  * detail returns ``download_url`` only when ``file_s3_key`` is set
  * soft-delete sets ``deleted_at`` and clears ``is_master``
  * error paths: blank title (400), missing resume (404), bad path id (400),
    unsupported content type (400), oversized content_length (400)
  * regression: LookupError → 404 (no logger.extra collision sneaking 500s through)
"""
from __future__ import annotations

import json


def test_list_resumes(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {
            "id": 1, "title": "Master", "summary": "summary",
            "is_master": 1, "file_s3_key": "users/sub/resumes/1/r.pdf",
            "original_filename": "r.pdf", "submission_count": 3,
        },
        {
            "id": 2, "title": "Backup", "summary": None,
            "is_master": 0, "file_s3_key": None,
            "original_filename": None, "submission_count": 0,
        },
    ]

    resp = resumes.handler(auth_event("GET /resumes"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 2
    assert body[0]["title"] == "Master"
    assert body[0]["is_master"] is True
    assert body[0]["has_file"] is True
    assert body[1]["has_file"] is False


def test_create_first_resume_auto_master(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """First resume for a user is auto-master even when the body omits the flag."""
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "title": "First", "summary": "s",
        "is_master": 1, "file_s3_key": None, "original_filename": None,
        "created_at": None, "updated_at": None, "submission_count": 0,
    }
    # Sequence:
    #   1. _has_any_resume → fetchone None     (no resumes yet)
    #   2. _detail SELECT resume               → detail_row
    mock_cursor.fetchone.side_effect = [None, detail_row]
    mock_cursor.fetchall.side_effect = [[]]  # _detail submissions
    type(mock_cursor).lastrowid = mocker.PropertyMock(return_value=1)

    resp = resumes.handler(
        auth_event("POST /resumes", body={"title": "First", "summary": "s"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["is_master"] is True
    # _clear_other_masters runs because first row is auto-master.
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("UPDATE resumes SET is_master = FALSE" in s for s in sql_calls)


def test_create_subsequent_resume_not_auto_master(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    detail_row = {
        "id": 2, "title": "Backup", "summary": None,
        "is_master": 0, "file_s3_key": None, "original_filename": None,
        "created_at": None, "updated_at": None, "submission_count": 0,
    }
    # Sequence:
    #   1. _has_any_resume → fetchone {"1": 1}  (already has one)
    #   2. _detail SELECT resume                 → detail_row
    mock_cursor.fetchone.side_effect = [{"1": 1}, detail_row]
    mock_cursor.fetchall.side_effect = [[]]
    type(mock_cursor).lastrowid = mocker.PropertyMock(return_value=2)

    resp = resumes.handler(
        auth_event("POST /resumes", body={"title": "Backup"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["is_master"] is False


def test_create_blank_title_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    resp = resumes.handler(
        auth_event("POST /resumes", body={"title": "   "}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "title is required" in json.loads(resp["body"])["error"]


def test_detail_returns_download_url_when_file_present(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")
    s3.generate_presigned_url.return_value = "https://signed-get/example"

    detail_row = {
        "id": 1, "title": "Master", "summary": "s",
        "is_master": 1,
        "file_s3_key": "users/sub/resumes/1/master.pdf",
        "original_filename": "master.pdf",
        "created_at": None, "updated_at": None, "submission_count": 0,
    }
    mock_cursor.fetchone.return_value = detail_row
    mock_cursor.fetchall.return_value = []

    resp = resumes.handler(
        auth_event("GET /resumes/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["download_url"] == "https://signed-get/example"
    s3.generate_presigned_url.assert_called_once()
    kwargs = s3.generate_presigned_url.call_args.kwargs
    assert kwargs["ClientMethod"] == "get_object"
    assert kwargs["Params"]["Key"] == "users/sub/resumes/1/master.pdf"
    assert "master.pdf" in kwargs["Params"]["ResponseContentDisposition"]


def test_detail_omits_download_url_when_no_file(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")

    mock_cursor.fetchone.return_value = {
        "id": 1, "title": "Text only", "summary": "s",
        "is_master": 0, "file_s3_key": None, "original_filename": None,
        "created_at": None, "updated_at": None, "submission_count": 0,
    }
    mock_cursor.fetchall.return_value = []

    resp = resumes.handler(auth_event("GET /resumes/{id}", path_id="1"), lambda_ctx)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["download_url"] is None
    s3.generate_presigned_url.assert_not_called()


def test_detail_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Regression: LookupError must not be re-classified as 500 by a logger collision."""
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None

    resp = resumes.handler(auth_event("GET /resumes/{id}", path_id="999"), lambda_ctx)

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "resume not found"


def test_invalid_path_id_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    resp = resumes.handler(
        auth_event("GET /resumes/{id}", path_id="abc"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_setting_master_clears_other_masters(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    detail_row = {
        "id": 2, "title": "New master", "summary": None,
        "is_master": 1, "file_s3_key": None, "original_filename": None,
        "created_at": None, "updated_at": None, "submission_count": 0,
    }
    # UPDATE rowcount=1, then _detail fetchone.
    mock_cursor.rowcount = 1
    mock_cursor.fetchone.side_effect = [detail_row]
    mock_cursor.fetchall.side_effect = [[]]

    resp = resumes.handler(
        auth_event(
            "PUT /resumes/{id}", path_id="2", body={"is_master": True},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    # Two distinct UPDATEs: the row's is_master=TRUE, then the clear-others.
    assert any("UPDATE resumes SET is_master = %s" in s for s in sql_calls) or any(
        "is_master = %s" in s for s in sql_calls
    )
    assert any(
        "UPDATE resumes SET is_master = FALSE" in s and "id != %s" in s
        for s in sql_calls
    )


def test_update_blank_title_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    resp = resumes.handler(
        auth_event("PUT /resumes/{id}", path_id="1", body={"title": "  "}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.rowcount = 0

    resp = resumes.handler(
        auth_event(
            "PUT /resumes/{id}", path_id="999", body={"title": "x"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_delete_resume_soft_deletes(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.rowcount = 1

    resp = resumes.handler(
        auth_event("DELETE /resumes/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["deleted"] is True
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "deleted_at = CURRENT_TIMESTAMP" in s and "is_master = FALSE" in s
        for s in sql_calls
    )


def test_delete_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.rowcount = 0

    resp = resumes.handler(
        auth_event("DELETE /resumes/{id}", path_id="999"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_upload_url_returns_presigned_put(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")
    s3.generate_presigned_url.return_value = "https://signed-put/example"

    # Resume row exists.
    mock_cursor.fetchone.return_value = {"id": 1}

    resp = resumes.handler(
        auth_event(
            "POST /resumes/{id}/upload-url",
            path_id="1",
            body={
                "content_type": "application/pdf",
                "original_filename": "My Resume v2.pdf",
                "content_length": 12345,
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["url"] == "https://signed-put/example"
    assert body["content_type"] == "application/pdf"
    # Sanitization replaces spaces; key is user-scoped via cognito sub.
    assert body["key"].startswith("users/user-sub-1/resumes/1/")
    assert body["key"].endswith(".pdf")
    assert " " not in body["key"]

    s3.generate_presigned_url.assert_called_once()
    put_kwargs = s3.generate_presigned_url.call_args.kwargs
    assert put_kwargs["ClientMethod"] == "put_object"
    assert put_kwargs["Params"]["ContentType"] == "application/pdf"
    # The handler also persisted the key + filename for download path.
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("UPDATE resumes SET file_s3_key" in s for s in sql_calls)


def test_upload_url_rejects_unknown_content_type(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = {"id": 1}

    resp = resumes.handler(
        auth_event(
            "POST /resumes/{id}/upload-url",
            path_id="1",
            body={"content_type": "application/msword"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_upload_url_rejects_oversized_content_length(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = {"id": 1}

    resp = resumes.handler(
        auth_event(
            "POST /resumes/{id}/upload-url",
            path_id="1",
            body={"content_length": 100 * 1024 * 1024},  # 100 MB
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_upload_url_resume_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # ownership check miss

    resp = resumes.handler(
        auth_event(
            "POST /resumes/{id}/upload-url",
            path_id="999",
            body={"content_type": "application/pdf"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_list_with_include_deleted_skips_deleted_at_filter(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {
            "id": 1, "title": "Live", "summary": None,
            "is_master": 0, "file_s3_key": None,
            "original_filename": None, "deleted_at": None,
            "submission_count": 0,
        },
        {
            "id": 2, "title": "Trashed", "summary": None,
            "is_master": 0, "file_s3_key": None,
            "original_filename": None,
            "deleted_at": "2026-04-29 10:00:00",
            "submission_count": 1,
        },
    ]

    resp = resumes.handler(
        auth_event("GET /resumes", qs={"include_deleted": "true"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 2
    assert body[0]["is_deleted"] is False
    assert body[1]["is_deleted"] is True
    assert body[1]["deleted_at"] == "2026-04-29 10:00:00"
    # The SQL must NOT have filtered deleted_at IS NULL.
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("WHERE r.user_id = %s\n" in s for s in sql_calls)
    assert not any(
        "WHERE r.user_id = %s AND r.deleted_at IS NULL" in s for s in sql_calls
    )


def test_list_default_filters_out_deleted(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    resp = resumes.handler(auth_event("GET /resumes"), lambda_ctx)

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("r.deleted_at IS NULL" in s for s in sql_calls)


def test_restore_clears_deleted_at(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "title": "Restored", "summary": None,
        "is_master": 0, "file_s3_key": None, "original_filename": None,
        "deleted_at": None, "created_at": None, "updated_at": None,
        "submission_count": 0,
    }
    mock_cursor.rowcount = 1
    mock_cursor.fetchone.return_value = detail_row
    mock_cursor.fetchall.return_value = []

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/restore", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["is_deleted"] is False
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "UPDATE resumes SET deleted_at = NULL" in s and "deleted_at IS NOT NULL" in s
        for s in sql_calls
    )


def test_restore_live_resume_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.rowcount = 0  # WHERE deleted_at IS NOT NULL matched nothing

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/restore", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_purge_deletes_s3_object_and_row(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")

    # The deleted-row lookup returns the file key.
    mock_cursor.fetchone.return_value = {
        "file_s3_key": "users/sub/resumes/1/r.pdf",
    }

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/purge", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["purged"] is True
    s3.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="users/sub/resumes/1/r.pdf"
    )
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("DELETE FROM resumes" in s for s in sql_calls)


def test_purge_no_file_skips_s3_delete(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")
    mock_cursor.fetchone.return_value = {"file_s3_key": None}

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/purge", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    s3.delete_object.assert_not_called()


def test_purge_live_resume_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Purge requires the row to already be soft-deleted."""
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # WHERE deleted_at IS NOT NULL miss

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/purge", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_purge_continues_when_s3_delete_fails(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """A missing S3 object should not block the row purge — best-effort."""
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)
    s3 = mocker.patch.object(resumes, "_s3")
    s3.delete_object.side_effect = RuntimeError("simulated S3 failure")
    mock_cursor.fetchone.return_value = {
        "file_s3_key": "users/sub/resumes/1/r.pdf",
    }

    resp = resumes.handler(
        auth_event("POST /resumes/{id}/purge", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("DELETE FROM resumes" in s for s in sql_calls)


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import resumes

    patched_conn("handlers.resumes")
    mocker.patch("handlers.resumes.get_user_id", return_value=42)

    resp = resumes.handler(
        auth_event("PATCH /resumes/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404