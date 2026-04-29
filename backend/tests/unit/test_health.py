"""Unit tests for ``handlers/health.py``.

The health handler is the smoke check for: JWT-claims extraction, an opened
DB connection, and a trivial ``SELECT 1``. Tests stub the connection and
verify the response carries through the auth claims plus the DB row.
"""
from __future__ import annotations

import json


def test_health_ok(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    from handlers import health

    patched_conn("handlers.health")
    mock_cursor.fetchone.return_value = {"ok": 1}

    resp = health.handler(auth_event("GET /health"), lambda_ctx)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {
        "ok": True,
        "user_sub": "user-sub-1",
        "user_email": "x@y.z",
        "db": {"ok": 1},
    }


def test_health_db_failure_propagates(mocker, patched_conn, mock_cursor, auth_event, lambda_ctx):
    """A DB error must bubble out — Lambda should fail visibly, not lie about health."""
    from handlers import health

    patched_conn("handlers.health")
    mock_cursor.execute.side_effect = RuntimeError("connection refused")

    try:
        health.handler(auth_event("GET /health"), lambda_ctx)
    except RuntimeError as e:
        assert "connection refused" in str(e)
    else:
        raise AssertionError("expected RuntimeError to propagate")