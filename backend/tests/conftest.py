"""Shared fixtures for backend unit tests.

Sets the env vars that ``common.db`` reads at import time and provides
MagicMock pymysql connection / cursor objects so handlers can be exercised
without RDS, S3, or any AWS credentials.

The ``patched_conn`` fixture is the usual entry point: call it with the
handler module's import path and it returns the connection mock with
``get_connection`` already patched to yield it.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("DB_HOST", "test-host")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "jobtracker_test")
os.environ.setdefault("DB_USER", "app")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("RESUME_BUCKET", "test-bucket")


@pytest.fixture
def mock_cursor() -> MagicMock:
    """A pymysql DictCursor stand-in.

    Returns
    -------
    MagicMock
        Cursor mock with safe defaults: ``fetchone`` returns ``None``,
        ``fetchall`` returns ``[]``, ``lastrowid`` is 1, ``rowcount`` is 0.
        Tests override these per-call as needed (often via ``side_effect``
        when a handler issues multiple SELECTs).
    """
    cur = MagicMock()
    cur.fetchone.return_value = None
    cur.fetchall.return_value = []
    cur.lastrowid = 1
    cur.rowcount = 0
    return cur


@pytest.fixture
def mock_conn(mock_cursor: MagicMock) -> MagicMock:
    """A pymysql connection whose ``cursor()`` context manager yields ``mock_cursor``."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = mock_cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn


@pytest.fixture
def patched_conn(mocker, mock_conn: MagicMock):
    """Patch ``get_connection`` on a named handler module to yield ``mock_conn``.

    Returns
    -------
    Callable[[str], MagicMock]
        Pass the handler's dotted module path (``"handlers.companies"``);
        receive the connection mock back so the test can configure cursor
        behavior as needed.
    """
    def _patch(module_path: str) -> MagicMock:
        ctx = MagicMock()
        ctx.__enter__.return_value = mock_conn
        ctx.__exit__.return_value = False
        mocker.patch(f"{module_path}.get_connection", return_value=ctx)
        return mock_conn
    return _patch


@pytest.fixture
def lambda_ctx() -> MagicMock:
    """A stand-in Lambda context with the attributes Powertools' logger reads.

    ``@logger.inject_lambda_context`` calls ``build_lambda_context_model``
    which dereferences ``function_name`` / ``memory_limit_in_mb`` /
    ``invoked_function_arn`` / ``aws_request_id`` on the context.
    """
    ctx = MagicMock()
    ctx.function_name = "test-fn"
    ctx.memory_limit_in_mb = 128
    ctx.invoked_function_arn = "arn:aws:lambda:us-west-2:000000000000:function:test-fn"
    ctx.aws_request_id = "req-1"
    return ctx


@pytest.fixture
def auth_event():
    """Build an HTTP API event with a Cognito JWT-claims envelope.

    Returns
    -------
    Callable
        ``auth_event(route_key, body=None, path_id=None, qs=None)`` →
        a Lambda event dict the handler can dispatch on.
    """
    def _build(
        route_key: str,
        *,
        body: dict | None = None,
        path_id: str | None = None,
        qs: dict[str, str] | None = None,
    ) -> dict:
        import json
        e: dict = {
            "routeKey": route_key,
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {"sub": "user-sub-1", "email": "x@y.z"}}
                }
            },
        }
        if body is not None:
            e["body"] = json.dumps(body)
        if path_id is not None:
            e["pathParameters"] = {"id": path_id}
        if qs is not None:
            e["queryStringParameters"] = qs
        return e
    return _build