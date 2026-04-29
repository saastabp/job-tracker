"""Unit tests for ``handlers/migrate.py``.

Uses ``tmp_path`` to stage fake migration files and points
``MIGRATIONS_DIR`` at it, so the test exercises the discovery + ordering +
tracking-table interactions without touching the bundled real migrations.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fake_migrations(tmp_path, mocker):
    """Stage SQL files in a tmp dir and patch handlers.migrate.MIGRATIONS_DIR.

    Returns the tmp path so tests can stage additional files.
    """
    (tmp_path / "0001_initial.sql").write_text("CREATE TABLE a (id INT);")
    (tmp_path / "0002_add_thing.sql").write_text("ALTER TABLE a ADD COLUMN b INT;")
    from handlers import migrate as migrate_mod
    mocker.patch.object(migrate_mod, "MIGRATIONS_DIR", tmp_path)
    return tmp_path


def test_migrate_applies_all_when_none_recorded(
    mocker, patched_conn, mock_cursor, fake_migrations, lambda_ctx,
):
    from handlers import migrate

    conn = patched_conn("handlers.migrate")
    mock_cursor.fetchall.return_value = []  # no rows in schema_migrations
    mock_cursor.nextset.return_value = False  # no extra result sets to drain

    result = migrate.handler({}, lambda_ctx)

    assert result == {"applied": ["0001_initial.sql", "0002_add_thing.sql"]}
    # Each apply commits — once for the tracking table, twice for the migrations.
    assert conn.commit.call_count == 3


def test_migrate_skips_already_applied(
    mocker, patched_conn, mock_cursor, fake_migrations, lambda_ctx,
):
    from handlers import migrate

    patched_conn("handlers.migrate")
    mock_cursor.fetchall.return_value = [{"version": "0001_initial.sql"}]
    mock_cursor.nextset.return_value = False

    result = migrate.handler({}, lambda_ctx)

    assert result == {"applied": ["0002_add_thing.sql"]}


def test_migrate_orders_files_lexicographically(
    mocker, patched_conn, mock_cursor, tmp_path, lambda_ctx,
):
    """Order matters — 0010 must apply after 0002, not before (string sort gotcha)."""
    from handlers import migrate as migrate_mod

    (tmp_path / "0010_later.sql").write_text("SELECT 1;")
    (tmp_path / "0002_earlier.sql").write_text("SELECT 1;")
    mocker.patch.object(migrate_mod, "MIGRATIONS_DIR", tmp_path)

    patched_conn("handlers.migrate")
    mock_cursor.fetchall.return_value = []
    mock_cursor.nextset.return_value = False

    result = migrate_mod.handler({}, lambda_ctx)

    assert result["applied"] == ["0002_earlier.sql", "0010_later.sql"]


def test_migrate_failure_rolls_back(
    mocker, patched_conn, mock_cursor, fake_migrations, lambda_ctx,
):
    """If a migration throws, the connection rolls back and the exception propagates."""
    from handlers import migrate

    conn = patched_conn("handlers.migrate")
    mock_cursor.fetchall.return_value = []
    mock_cursor.nextset.return_value = False
    # First execute is the CREATE TABLE schema_migrations (succeeds), then the
    # SELECT (succeeds), then the migration file's own execute should fail.
    mock_cursor.execute.side_effect = [
        None,                         # _ensure_tracking_table CREATE
        None,                         # _applied_versions SELECT
        RuntimeError("DDL error"),    # 0001_initial.sql apply
    ]

    with pytest.raises(RuntimeError, match="DDL error"):
        migrate.handler({}, lambda_ctx)

    conn.rollback.assert_called()