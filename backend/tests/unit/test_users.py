"""Unit tests for ``common/users.py``.

The ``get_user_id`` helper resolves a Cognito sub to the local ``users.id``.
A miss is a hard error (``UserNotFoundError``, a ``LookupError`` subclass) —
the post-confirmation trigger guarantees a row exists, so a missing one is a
real bug, not a sign-up race.
"""
from __future__ import annotations

import pytest

from common.users import UserNotFoundError, get_user_id


def test_get_user_id_returns_int(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = {"id": 42}

    assert get_user_id(mock_conn, "abc-sub") == 42

    sql, params = mock_cursor.execute.call_args.args
    assert "SELECT id FROM users" in sql
    assert "deleted_at IS NULL" in sql
    assert params == ("abc-sub",)


def test_get_user_id_missing_raises_lookup_error(mock_conn, mock_cursor):
    mock_cursor.fetchone.return_value = None

    with pytest.raises(UserNotFoundError) as ei:
        get_user_id(mock_conn, "missing-sub")
    assert "missing-sub" in str(ei.value)
    # UserNotFoundError must subclass LookupError so handlers' except-LookupError
    # branches catch it and translate to 404.
    assert isinstance(ei.value, LookupError)