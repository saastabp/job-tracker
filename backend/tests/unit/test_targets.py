"""Unit tests for ``handlers/targets.py``.

Targets is the only handler that dispatches on ``requestContext.http.method``
(GET vs PUT) rather than ``routeKey``, so the event factory needs a small
override. Tests cover catalog + goals join, upsert with validation, and
405 on unsupported methods.
"""
from __future__ import annotations

import json


def _http_event(method: str, *, body: dict | None = None) -> dict:
    e: dict = {
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
            },
            "http": {"method": method},
        },
    }
    if body is not None:
        e["body"] = json.dumps(body)
    return e


def test_get_targets_returns_catalog_with_goals(
    mocker, patched_conn, mock_cursor, lambda_ctx,
):
    from handlers import targets

    patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)
    mock_cursor.fetchall.return_value = [
        {"id": 1, "short_name": "submissions",      "description": "Submissions",
         "daily": 5, "weekly": 25},
        {"id": 2, "short_name": "personal_outreach", "description": "Personal Outreach",
         "daily": None, "weekly": 10},
        {"id": 3, "short_name": "recruiter_outreach", "description": "Recruiter Outreach",
         "daily": None, "weekly": None},
    ]

    resp = targets.handler(_http_event("GET"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["types"]) == 3
    assert body["types"][0] == {
        "id": 1, "short_name": "submissions", "description": "Submissions",
        "daily": 5, "weekly": 25,
    }
    assert body["types"][1]["daily"] is None
    assert body["types"][2]["weekly"] is None


def test_put_targets_upserts_then_returns_get_shape(
    mocker, patched_conn, mock_cursor, lambda_ctx,
):
    from handlers import targets

    conn = patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)
    # _put runs N upsert INSERTs, then _get runs the catalog SELECT.
    mock_cursor.fetchall.return_value = [
        {"id": 1, "short_name": "submissions", "description": "Submissions",
         "daily": 5, "weekly": 25},
    ]

    resp = targets.handler(
        _http_event(
            "PUT",
            body={
                "goals": [
                    {"target_type_id": 1, "cadence": "daily",  "goal_count": 5},
                    {"target_type_id": 1, "cadence": "weekly", "goal_count": 25},
                ],
            },
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["types"][0]["daily"] == 5
    # 2 upserts + 1 SELECT = 3 execute calls, plus a commit on the upsert path.
    assert mock_cursor.execute.call_count == 3
    conn.commit.assert_called_once()


def test_put_invalid_cadence_returns_400(
    mocker, patched_conn, mock_cursor, lambda_ctx,
):
    from handlers import targets

    patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)

    resp = targets.handler(
        _http_event(
            "PUT",
            body={"goals": [
                {"target_type_id": 1, "cadence": "monthly", "goal_count": 5},
            ]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "cadence must be one of" in json.loads(resp["body"])["error"]


def test_put_negative_goal_returns_400(
    mocker, patched_conn, mock_cursor, lambda_ctx,
):
    from handlers import targets

    patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)

    resp = targets.handler(
        _http_event(
            "PUT",
            body={"goals": [
                {"target_type_id": 1, "cadence": "daily", "goal_count": -1},
            ]},
        ),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400
    assert "goal_count must be >= 0" in json.loads(resp["body"])["error"]


def test_put_goals_must_be_list_returns_400(
    mocker, patched_conn, lambda_ctx,
):
    from handlers import targets

    patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)

    resp = targets.handler(
        _http_event("PUT", body={"goals": "not-a-list"}),
        lambda_ctx,
    )

    assert resp["statusCode"] == 400


def test_unsupported_method_returns_405(
    mocker, patched_conn, lambda_ctx,
):
    from handlers import targets

    patched_conn("handlers.targets")
    mocker.patch("handlers.targets.get_user_id", return_value=42)

    resp = targets.handler(_http_event("DELETE"), lambda_ctx)

    assert resp["statusCode"] == 405