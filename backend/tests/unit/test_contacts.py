"""Unit tests for ``handlers/contacts.py``.

Mocks the pymysql cursor and ``get_user_id`` to focus on route dispatch,
catalog resolution (kind / method / direction), and the outreach-event
log/delete paths.

Notable: ``test_lookup_error_returns_404_not_500`` is a regression for the
``extra={"msg": str(e)}`` LogRecord-attribute collision that turned 404
paths into 500s in slice 03.
"""
from __future__ import annotations

import json


def test_list_contacts(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {
            "id": 1, "name": "Jane Doe", "email": "jane@example.com",
            "linkedin_url": None, "notes": None,
            "company_id": 7, "company_name": "Acme",
            "kind": "personal", "primary_method": "email",
            "outreach_count": 3,
            "last_outreach_at": "2026-04-20 10:00:00",
        },
        {
            "id": 2, "name": "Sam Recruiter", "email": None,
            "linkedin_url": "https://linkedin.com/in/sam", "notes": None,
            "company_id": None, "company_name": None,
            "kind": "recruiter", "primary_method": "linkedin",
            "outreach_count": 0, "last_outreach_at": None,
        },
    ]

    resp = contacts.handler(auth_event("GET /contacts"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body) == 2
    assert body[0]["kind"] == "personal"
    assert body[0]["company_name"] == "Acme"
    assert body[0]["outreach_count"] == 3
    assert body[0]["last_outreach_at"] == "2026-04-20 10:00:00"
    assert body[1]["last_outreach_at"] is None


def test_list_filters_by_kind(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    resp = contacts.handler(
        auth_event("GET /contacts", qs={"kind": "recruiter"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("ck.short_name = %s" in s for s in sql_calls)


def test_list_filters_by_company(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = []

    resp = contacts.handler(
        auth_event("GET /contacts", qs={"company_id": "7"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("c.company_id = %s" in s for s in sql_calls)


def test_create_contact(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    detail_row = {
        "id": 99, "name": "Jane", "email": None, "linkedin_url": None,
        "notes": None, "company_id": None, "company_name": None,
        "kind": "personal", "primary_method": None,
        "outreach_count": 0, "last_outreach_at": None,
    }
    # Sequence of fetchone:
    #   1. _catalog_id(contact_kinds 'personal') → {"id": 1}
    #   2. _detail SELECT contact                → detail_row
    mock_cursor.fetchone.side_effect = [{"id": 1}, detail_row]
    mock_cursor.fetchall.side_effect = [[], []]  # outreach, linked_submissions
    mock_cursor.lastrowid = 99

    resp = contacts.handler(
        auth_event("POST /contacts", body={"name": "Jane", "kind": "personal"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["id"] == 99
    assert body["kind"] == "personal"
    assert body["outreach"] == []


def test_create_blank_name_returns_400(
    mocker, patched_conn, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    resp = contacts.handler(
        auth_event("POST /contacts", body={"name": "  "}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "name is required" in json.loads(resp["body"])["error"]


def test_create_unknown_kind_returns_400(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # catalog miss

    resp = contacts.handler(
        auth_event(
            "POST /contacts",
            body={"name": "Jane", "kind": "frenemy"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_create_with_company_verifies_ownership(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    # Sequence:
    #   1. _catalog_id contact_kinds → {"id": 1}
    #   2. _verify_company           → None (miss)
    mock_cursor.fetchone.side_effect = [{"id": 1}, None]

    resp = contacts.handler(
        auth_event(
            "POST /contacts",
            body={"name": "Jane", "kind": "personal", "company_id": 999},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "company_id 999 not found" in json.loads(resp["body"])["error"]


def test_detail_with_outreach_timeline(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    mock_cursor.fetchone.return_value = {
        "id": 1, "name": "Jane", "email": "jane@x.com", "linkedin_url": None,
        "notes": "warm intro", "company_id": 7, "company_name": "Acme",
        "kind": "personal", "primary_method": "email",
        "outreach_count": 2, "last_outreach_at": "2026-04-25 09:00:00",
    }
    mock_cursor.fetchall.side_effect = [
        [
            {
                "id": 11, "outreach_at": "2026-04-25 09:00:00",
                "method": "email", "direction": "outbound", "notes": "follow-up",
            },
            {
                "id": 10, "outreach_at": "2026-04-20 14:30:00",
                "method": "linkedin", "direction": "outbound", "notes": None,
            },
        ],
        [
            {
                "id": 42, "role_title": "SRE",
                "company_name": "Acme", "status": "applied",
            },
        ],
    ]

    resp = contacts.handler(
        auth_event("GET /contacts/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["name"] == "Jane"
    assert body["outreach_count"] == 2
    assert len(body["outreach"]) == 2
    assert body["outreach"][0]["method"] == "email"
    assert body["outreach"][0]["direction"] == "outbound"
    assert body["linked_submissions"] == [
        {"id": 42, "role_title": "SRE", "company_name": "Acme", "status": "applied"},
    ]


def test_detail_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Regression: LookupError must not be re-classified as 500 by a logger collision."""
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None

    resp = contacts.handler(
        auth_event("GET /contacts/{id}", path_id="999"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"] == "contact not found"


def test_invalid_path_id_returns_400(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    resp = contacts.handler(
        auth_event("GET /contacts/{id}", path_id="not-an-int"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_update_each_mutable_field(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "name": "Jane Updated", "email": "new@x.com", "linkedin_url": None,
        "notes": "edited", "company_id": None, "company_name": None,
        "kind": "recruiter", "primary_method": "phone",
        "outreach_count": 0, "last_outreach_at": None,
    }
    # Sequence:
    #   1. _catalog_id contact_kinds 'recruiter'    → {"id": 2}
    #   2. _catalog_id outreach_methods 'phone'     → {"id": 3}
    #   3. _detail SELECT contact                   → detail_row
    mock_cursor.fetchone.side_effect = [{"id": 2}, {"id": 3}, detail_row]
    mock_cursor.fetchall.side_effect = [[], []]
    mock_cursor.rowcount = 1

    resp = contacts.handler(
        auth_event(
            "PUT /contacts/{id}",
            path_id="1",
            body={
                "name": "Jane Updated",
                "kind": "recruiter",
                "primary_method": "phone",
                "email": "new@x.com",
                "notes": "edited",
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    update_sql = next(s for s in sql_calls if s.startswith("UPDATE contacts SET"))
    assert "name = %s" in update_sql
    assert "contact_kind_id = %s" in update_sql
    assert "primary_method_id = %s" in update_sql
    assert "email = %s" in update_sql
    assert "notes = %s" in update_sql


def test_update_clear_primary_method(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    """Passing ``primary_method: null`` should set the column to NULL."""
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "name": "Jane", "email": None, "linkedin_url": None,
        "notes": None, "company_id": None, "company_name": None,
        "kind": "personal", "primary_method": None,
        "outreach_count": 0, "last_outreach_at": None,
    }
    mock_cursor.fetchone.side_effect = [detail_row]
    mock_cursor.fetchall.side_effect = [[], []]
    mock_cursor.rowcount = 1

    resp = contacts.handler(
        auth_event(
            "PUT /contacts/{id}",
            path_id="1",
            body={"primary_method": None},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "primary_method_id = NULL" in s for s in sql_calls
    )


def test_update_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.rowcount = 0

    resp = contacts.handler(
        auth_event("PUT /contacts/{id}", path_id="999", body={"name": "x"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_delete_soft_deletes(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.rowcount = 1

    resp = contacts.handler(
        auth_event("DELETE /contacts/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["deleted"] is True
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("deleted_at = CURRENT_TIMESTAMP" in s for s in sql_calls)


def test_delete_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.rowcount = 0

    resp = contacts.handler(
        auth_event("DELETE /contacts/{id}", path_id="999"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_log_outreach_with_method_and_direction(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "name": "Jane", "email": None, "linkedin_url": None,
        "notes": None, "company_id": None, "company_name": None,
        "kind": "personal", "primary_method": None,
        "outreach_count": 1, "last_outreach_at": "2026-04-29 12:00:00",
    }
    # Sequence of fetchone:
    #   1. _verify_contact                          → {"id": 1}
    #   2. _catalog_id outreach_directions 'inbound'→ {"id": 2}
    #   3. _catalog_id outreach_methods 'email'     → {"id": 1}
    #   4. _detail SELECT contact                   → detail_row
    mock_cursor.fetchone.side_effect = [
        {"id": 1}, {"id": 2}, {"id": 1}, detail_row,
    ]
    mock_cursor.fetchall.side_effect = [[], []]

    resp = contacts.handler(
        auth_event(
            "POST /contacts/{id}/outreach",
            path_id="1",
            body={
                "method": "email",
                "direction": "inbound",
                "notes": "they replied",
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "INSERT INTO contact_outreach" in s and "outreach_method_id" in s
        for s in sql_calls
    )


def test_log_outreach_uses_explicit_timestamp(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    detail_row = {
        "id": 1, "name": "Jane", "email": None, "linkedin_url": None,
        "notes": None, "company_id": None, "company_name": None,
        "kind": "personal", "primary_method": None,
        "outreach_count": 0, "last_outreach_at": None,
    }
    # Sequence: _verify_contact → _catalog_id direction 'outbound' → _detail
    mock_cursor.fetchone.side_effect = [{"id": 1}, {"id": 1}, detail_row]
    mock_cursor.fetchall.side_effect = [[], []]

    resp = contacts.handler(
        auth_event(
            "POST /contacts/{id}/outreach",
            path_id="1",
            body={"outreach_at": "2026-04-25 09:00:00"},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    insert_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO contact_outreach" in c.args[0]
    ]
    assert len(insert_calls) == 1
    # The timestamp is in the params tuple, not the SQL string.
    params = insert_calls[0].args[1]
    assert "2026-04-25 09:00:00" in params


def test_log_outreach_contact_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.fetchone.return_value = None  # _verify_contact miss

    resp = contacts.handler(
        auth_event("POST /contacts/{id}/outreach", path_id="999", body={}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_delete_outreach_soft_deletes(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.rowcount = 1

    resp = contacts.handler(
        {
            "routeKey": "DELETE /contacts/{contact_id}/outreach/{id}",
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
                }
            },
            "pathParameters": {"contact_id": "1", "id": "11"},
        },
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["deleted"] is True
    sql_calls = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any(
        "UPDATE contact_outreach SET deleted_at = CURRENT_TIMESTAMP" in s
        for s in sql_calls
    )


def test_delete_outreach_not_found_returns_404(
    mocker, patched_conn, mock_cursor, auth_event, lambda_ctx,
):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)
    mock_cursor.rowcount = 0

    resp = contacts.handler(
        {
            "routeKey": "DELETE /contacts/{contact_id}/outreach/{id}",
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
                }
            },
            "pathParameters": {"contact_id": "1", "id": "999"},
        },
        lambda_ctx,
    )

    assert resp["statusCode"] == 404


def test_unknown_route_returns_404(mocker, patched_conn, auth_event, lambda_ctx):
    from handlers import contacts

    patched_conn("handlers.contacts")
    mocker.patch("handlers.contacts.get_user_id", return_value=42)

    resp = contacts.handler(
        auth_event("PATCH /contacts/{id}", path_id="1"),
        lambda_ctx,
    )

    assert resp["statusCode"] == 404