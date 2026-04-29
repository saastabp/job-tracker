"""RDS IAM-auth MySQL connection helper.

Reads connection details from env vars (set by the API stack from SSM at deploy
time). Generates a fresh 15-minute IAM auth token per connection, opens a TLS
pymysql connection verified against the bundled RDS CA bundle.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import boto3
import pymysql
from pymysql.cursors import DictCursor

from common.logger import logger

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ["DB_PORT"])
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ.get("DB_USER", "app")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")

CA_BUNDLE = Path(__file__).resolve().parents[1] / "rds-ca-bundle.pem"

_rds = boto3.client("rds", region_name=AWS_REGION)


def _auth_token() -> str:
    return _rds.generate_db_auth_token(
        DBHostname=DB_HOST,
        Port=DB_PORT,
        DBUsername=DB_USER,
        Region=AWS_REGION,
    )


@contextmanager
def get_connection(*, client_flag: int = 0) -> Iterator[Any]:
    if not CA_BUNDLE.exists():
        raise RuntimeError(
            f"RDS CA bundle not found at {CA_BUNDLE}. "
            "Run `make fetch-rds-ca` before sam build."
        )
    try:
        logger.info("db: generating auth token", extra={"db_host": DB_HOST, "db_user": DB_USER})
        token = _auth_token()
        logger.info("db: auth token generated, opening pymysql connection")
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=token,
            database=DB_NAME,
            ssl={"ca": str(CA_BUNDLE)},
            connect_timeout=5,
            cursorclass=DictCursor,
            client_flag=client_flag,
        )
        logger.info("db: connection opened")
    except Exception:
        logger.exception("db: connection setup failed", extra={"db_host": DB_HOST, "db_user": DB_USER})
        raise
    try:
        yield conn
    finally:
        conn.close()