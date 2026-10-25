"""Synchronous SQLite sessions, with explicit transactions and foreign keys."""

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, create_engine, event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from still.models import Base, WorkerLease


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
    # Columns added after a table already exists are not covered by create_all.
    added = {row[1] for row in current.connection().exec_driver_sql("PRAGMA table_info(deliveries)")}
    if "contents" not in added:
        current.connection().exec_driver_sql("ALTER TABLE deliveries ADD COLUMN contents TEXT NOT NULL DEFAULT ''")
    added = {row[1] for row in current.connection().exec_driver_sql("PRAGMA table_info(relationships)")}
    if "last_returned_at" not in added:
        current.connection().exec_driver_sql("ALTER TABLE relationships ADD COLUMN last_returned_at TEXT")
        # Older history cannot say which lender the last return belonged to, so everyone
        # who has returned notes keeps the throttle rather than getting it forgiven.
        current.connection().exec_driver_sql(
            "UPDATE relationships SET last_returned_at = (SELECT last_returned_at FROM personal_life WHERE id = 1) "
            "WHERE returns > 0 AND (SELECT last_returned_at FROM personal_life WHERE id = 1) IS NOT NULL"
        )
    # SQLite triggers protect entries even from SQL outside the ORM.
    for action in ("UPDATE", "DELETE"):
        current.connection().exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS entries_no_{action.lower()} BEFORE {action} ON entries "
            "BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END"
        )


def require_owner(current: Session, owner: str | None) -> None:
    if owner is not None:
        lease = current.get(WorkerLease, 1)
        if lease is None or lease.owner != owner:
            raise RuntimeError("Still worker lost its database lease")


def as_dict(row: Base) -> dict:
    return {column.key: getattr(row, column.key) for column in inspect(row).mapper.column_attrs}
