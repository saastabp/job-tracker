"""Unit tests for ``handlers/companies.py``.

Mocks the pymysql cursor and ``get_user_id`` to focus on route dispatch,
request/response shape, and error paths.

Notable: ``test_lookup_error_returns_404_not_500`` is a regression for the
slice-03 ``extra={"msg": str(e)}`` LogRecord-attribute collision that turned
404 paths into 500s.
"""
from __future__ import annotations

import json


def test_list_companies(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {"id": 1, "name": "Acme",    "notes": None,   "submission_count": 3},
        {"id": 2, "name": "Beta Co", "notes": "warm", "submission_count": 1},
    ]

    resp = companies.handler(auth_event("GET /companies"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 2
    assert body[0] == {
        "id": 1, "name": "Acme", "notes": None, "submission_count": 3,
    }


def test_create_company(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)
    mock_cursor.lastrowid = 99
    # _detail is called after insert; first fetchone returns the row, fetchall
    # returns the (empty) submissions list.
    mock_cursor.fetchone.return_value = {
        "id": 99, "name": "Acme", "notes": None, "submission_count": 0,
    }
    mock_cursor.fetchall.return_value = []

    resp = companies.handler(
        auth_event("POST /companies", body={"name": "Acme"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["id"] == 99
    assert body["name"] == "Acme"
    assert body["submissions"] == []


def test_create_company_blank_name_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)

    resp = companies.handler(
        auth_event("POST /companies", body={"name": "   "}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "name is required" in json.loads(resp["body"])["error"]


def test_lookup_error_returns_404_not_500(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Regression: slice-03 logger.extra collision turned this 404 into a 500."""
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # company row missing

    resp = companies.handler(
        auth_event("GET /companies/{id}", path_id="123"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "company not found"


def test_invalid_path_id_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)

    resp = companies.handler(
        auth_event("GET /companies/{id}", path_id="not-an-int"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_company(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)
    mock_cursor.rowcount = 1
    mock_cursor.fetchone.return_value = {
        "id": 1, "name": "Acme New", "notes": "updated", "submission_count": 0,
    }
    mock_cursor.fetchall.return_value = []

    resp = companies.handler(
        auth_event(
            "PUT /companies/{id}",
            path_id="1",
            body={"name": "Acme New", "notes": "updated"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["name"] == "Acme New"


def test_update_blank_name_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)

    resp = companies.handler(
        auth_event("PUT /companies/{id}", path_id="1", body={"name": "  "}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import companies

    patched_conn("handlers.companies")
    mocker.patch("handlers.companies.get_user_id", return_value=42)

    resp = companies.handler(
        auth_event("DELETE /companies/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404