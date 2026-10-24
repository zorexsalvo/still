"""Synchronous SQLite sessions, with explicit transactions and foreign keys."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from still.models import Base


@contextmanager
def session(path: Path, *, readonly: bool = False, immediate: bool = False):
    url = URL.create(
        "sqlite+pysqlite",
        database=path.resolve().as_uri() if readonly else str(path.resolve()),
        query={"mode": "ro", "uri": "true"} if readonly else {},
    )
    engine = create_engine(url, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        # Explicit BEGIN also gives Python's legacy sqlite driver transactional DDL/reads.
        connection.isolation_level = None
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            if not readonly:
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    try:
        with Session(engine) as current, current.begin():
            yield current
    finally:
        engine.dispose()


def create_tables(current: Session) -> None:
    Base.metadata.create_all(current.connection())
    # SQLite triggers protect entries even from SQL outside the ORM.
    for action in ("UPDATE", "DELETE"):
        current.connection().exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS entries_no_{action.lower()} BEFORE {action} ON entries "
            "BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END"
        )


def as_dict(row: Base) -> dict:
    return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}
