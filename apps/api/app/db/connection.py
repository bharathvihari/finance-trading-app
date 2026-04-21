from __future__ import annotations

from typing import Generator

import psycopg2
import psycopg2.extensions

from app.core.config import settings


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


def get_db() -> Generator[psycopg2.extensions.connection, None, None]:
    """FastAPI dependency that yields a psycopg2 connection.

    Commits on success, rolls back on any exception, always closes.
    """
    conn = _connect()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()
