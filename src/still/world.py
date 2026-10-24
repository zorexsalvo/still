"""Persistent starting world; the model never owns these facts."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from still.clock import timestamp


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS places (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('depot', 'office', 'establishment'))
    )""",
    """CREATE TABLE IF NOT EXISTS people (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        place_id TEXT NOT NULL REFERENCES places(id)
    )""",
    """CREATE TABLE IF NOT EXISTS courier (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        place_id TEXT NOT NULL REFERENCES places(id),
        started_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY,
        sender_id TEXT NOT NULL REFERENCES people(id),
        recipient_id TEXT NOT NULL REFERENCES people(id),
        origin_id TEXT NOT NULL REFERENCES places(id),
        destination_id TEXT NOT NULL REFERENCES places(id),
        created_at TEXT NOT NULL,
        due_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'carried', 'delivered', 'returned', 'lost')),
        CHECK (origin_id != destination_id),
        CHECK (due_at > created_at)
    )""",
    """CREATE TABLE IF NOT EXISTS ticks (
        id INTEGER PRIMARY KEY,
        tick_key TEXT NOT NULL UNIQUE,
        at TEXT NOT NULL,
        action TEXT NOT NULL,
        hardware TEXT NOT NULL,
        publish INTEGER NOT NULL DEFAULT 0 CHECK (publish IN (0, 1))
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        tick_id INTEGER NOT NULL REFERENCES ticks(id),
        kind TEXT NOT NULL,
        facts TEXT NOT NULL,
        event_key TEXT UNIQUE
    )""",
    """CREATE TABLE IF NOT EXISTS encounters (
        person_id TEXT PRIMARY KEY REFERENCES people(id),
        first_at TEXT NOT NULL,
        last_at TEXT NOT NULL,
        count INTEGER NOT NULL CHECK (count > 0)
    )""",
    """CREATE TABLE IF NOT EXISTS publications (
        tick_id INTEGER PRIMARY KEY REFERENCES ticks(id),
        status TEXT NOT NULL CHECK (status IN ('silent', 'writing', 'published', 'failed')),
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY,
        tick_id INTEGER NOT NULL UNIQUE REFERENCES ticks(id),
        at TEXT NOT NULL,
        text TEXT NOT NULL CHECK (length(trim(text)) > 0)
    )""",
    """CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
        BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
        BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END""",
    """CREATE TABLE IF NOT EXISTS arrivals (
        tick_id INTEGER NOT NULL REFERENCES ticks(id),
        person_id TEXT NOT NULL REFERENCES people(id),
        PRIMARY KEY (tick_id, person_id)
    )""",
)


def initialize(path: Path, now: datetime) -> None:
    created_at = timestamp(now)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA:
            connection.execute(statement)
        if connection.execute("SELECT 1 FROM courier WHERE id = 1").fetchone():
            return
        connection.executemany(
            "INSERT INTO places (id, name, kind) VALUES (?, ?, ?)",
            [
                ("depot", "Depot", "depot"),
                ("records-office", "Records office", "office"),
                ("repair-shop", "Repair shop", "establishment"),
            ],
        )
        connection.executemany(
            "INSERT INTO people (id, name, role, place_id) VALUES (?, ?, ?, ?)",
            [
                ("mara", "Mara", "receptionist", "records-office"),
                ("ivo", "Ivo", "repairer", "repair-shop"),
            ],
        )
        connection.execute(
            "INSERT INTO courier (id, place_id, started_at) VALUES (1, 'depot', ?)",
            (created_at,),
        )
        connection.executemany(
            "INSERT INTO deliveries "
            "(id, sender_id, recipient_id, origin_id, destination_id, created_at, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("parcel-1", "ivo", "mara", "repair-shop", "records-office",
                 created_at, timestamp(now + timedelta(hours=4))),
                ("parcel-2", "mara", "ivo", "records-office", "repair-shop",
                 created_at, timestamp(now + timedelta(hours=6))),
            ],
        )


def snapshot(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        courier = connection.execute("SELECT * FROM courier WHERE id = 1").fetchone()
        return {
            "timezone": "UTC",
            "courier": dict(courier),
            "places": [dict(row) for row in connection.execute("SELECT * FROM places ORDER BY id")],
            "people": [dict(row) for row in connection.execute("SELECT * FROM people ORDER BY id")],
            "deliveries": [dict(row) for row in connection.execute(
                "SELECT * FROM deliveries ORDER BY due_at, id"
            )],
        }
