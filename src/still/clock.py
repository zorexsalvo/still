"""World time is real UTC time; sleep classification applies only to downtime."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, time, timedelta
from pathlib import Path


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("world timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def split_downtime(start: datetime, end: datetime):
    start = datetime.fromisoformat(timestamp(start))
    end = datetime.fromisoformat(timestamp(end))
    if end < start:
        raise ValueError("downtime cannot end before it starts")
    while start < end:
        if start.hour < 6:
            kind = "sleep"
            boundary = datetime.combine(start.date(), time(6), UTC)
        elif start.hour < 22:
            kind = "mundane"
            boundary = datetime.combine(start.date(), time(22), UTC)
        else:
            kind = "sleep"
            boundary = datetime.combine(start.date() + timedelta(days=1), time(6), UTC)
        stop = min(boundary, end)
        yield start, stop, kind
        start = stop


def resume(path: Path, now: datetime) -> None:
    current = timestamp(now)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS world_clock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seen TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS downtime (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('sleep', 'mundane'))
        )""")
        row = connection.execute("SELECT last_seen FROM world_clock WHERE id = 1").fetchone()
        if row is not None:
            for start, end, kind in split_downtime(datetime.fromisoformat(row[0]), now):
                connection.execute(
                    "INSERT INTO downtime (started_at, ended_at, kind) VALUES (?, ?, ?)",
                    (timestamp(start), timestamp(end), kind),
                )
        connection.execute(
            "INSERT INTO world_clock (id, last_seen) VALUES (1, ?) "
            "ON CONFLICT (id) DO UPDATE SET last_seen = excluded.last_seen",
            (current,),
        )


def checkpoint(path: Path, now: datetime) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE world_clock SET last_seen = MAX(last_seen, ?) WHERE id = 1",
            (timestamp(now),),
        )
