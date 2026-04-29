"""MigrationRunner Lambda — applies pending SQL migrations.

Connects via RDS IAM auth as the ``app`` user (which holds ``ALL PRIVILEGES``
on the ``jobtracker`` database, including DDL, per the README bootstrap).
Executes any ``migrations/*.sql`` files not yet recorded in
``schema_migrations``, and records each one on success. Forward-only.

Notes
-----
Migration files live at ``backend/src/migrations/`` and are bundled into the
Lambda artifact by SAM's standard Python builder. At runtime they resolve to
``/var/task/migrations/``.

Multi-statement SQL files are supported via ``CLIENT.MULTI_STATEMENTS``.
Note that DDL in MySQL implicitly commits, so a file that fails partway
through DDL leaves a partial schema; the operator must drop the
already-created tables before re-running.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pymysql.constants import CLIENT

from common.db import get_connection
from common.logger import logger

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _ensure_tracking_table(conn: Any) -> None:
    logger.info("migrate: ensuring schema_migrations table exists")
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  version VARCHAR(255) NOT NULL PRIMARY KEY,"
            "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    conn.commit()


def _applied_versions(conn: Any) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row["version"] for row in cur.fetchall()}


def _drain_results(cur: Any) -> None:
    while cur.nextset():
        pass


def _apply(conn: Any, name: str, sql: str) -> None:
    logger.info("migrate: applying", extra={"version": name, "bytes": len(sql)})
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            _drain_results(cur)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (name,),
            )
        conn.commit()
        logger.info("migrate: applied", extra={"version": name})
    except Exception:
        logger.exception("migrate: failed", extra={"version": name})
        conn.rollback()
        raise


@logger.inject_lambda_context(log_event=False)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    logger.info("migrate: enter", extra={"migrations_dir": str(MIGRATIONS_DIR)})
    try:
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        logger.info("migrate: discovered files", extra={"count": len(files)})

        with get_connection(client_flag=CLIENT.MULTI_STATEMENTS) as conn:
            _ensure_tracking_table(conn)
            applied = _applied_versions(conn)
            logger.info("migrate: already applied", extra={"count": len(applied)})

            newly_applied: list[str] = []
            for path in files:
                if path.name in applied:
                    logger.info("migrate: skip (already applied)", extra={"version": path.name})
                    continue
                sql = path.read_text(encoding="utf-8")
                _apply(conn, path.name, sql)
                newly_applied.append(path.name)

        logger.info("migrate: exit ok", extra={"applied": newly_applied})
        return {"applied": newly_applied}
    except Exception:
        logger.exception("migrate: top-level failure")
        raise