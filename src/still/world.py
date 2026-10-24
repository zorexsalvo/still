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
