import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from still.db import session
from still.models import Courier, Delivery, Encounter, Entry, Event, Tick, WorldClock
from still.simulation import tick
from still.world import initialize, snapshot


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "still ?# diary.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)

    def test_legacy_schema_and_all_history_are_preserved(self):
        fixture = Path(__file__).parent / "fixtures" / "legacy.sql"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(fixture.read_text())
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            before = {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}
            columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] for table in tables}
        initialize(self.path, self.now)
        initialize(self.path, self.now)
        with closing(sqlite3.connect(self.path)) as connection:
            for table, rows in before.items():
                # A migration may add a column; it may not drop, rename or rewrite one.
                legacy = ", ".join(f'"{name}"' for name in columns[table])
                self.assertEqual(
                    connection.execute(f'SELECT {legacy} FROM "{table}" ORDER BY rowid').fetchall(), rows
                )
            after = [row[1] for row in connection.execute('PRAGMA table_info("deliveries")')]
            self.assertLessEqual(set(columns["deliveries"]), set(after))
            surviving = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertLessEqual(set(tables), surviving)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.get(Tick, 1).action, {"kind": "move", "place_id": "repair-shop"})
            self.assertEqual(db.get(Tick, 1).hardware, {"temperature_c": None})
            self.assertEqual(db.get(Event, 1).facts["person_id"], "ivo")
            self.assertEqual(db.get(Encounter, "ivo").count, 3)
            self.assertEqual(db.get(Delivery, "parcel-1").recipient.name, "Mara")
            self.assertEqual(db.get(Entry, 1).text, "I met Ivo at the Repair shop.")
            self.assertEqual(db.get(WorldClock, 1).last_seen, "2026-10-24T10:00:00.000000+00:00")
        result = tick(self.path, "after-migration", self.now, {"kind": "deliver", "delivery_id": "parcel-2"})
        self.assertEqual(result["id"], 2)
        self.assertIn("inspection", [event["kind"] for event in result["events"]])
        with session(self.path) as db:
            self.assertEqual(db.get(Entry, 1).text, before["entries"][0][3])

    def test_foreign_keys_and_immutable_entries_apply_to_orm_writes(self):
        fixture = Path(__file__).parent / "fixtures" / "legacy.sql"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(fixture.read_text())
        for operation in ("foreign_key", "entry_update", "entry_delete"):
            with self.subTest(operation=operation), self.assertRaises(IntegrityError):
                with session(self.path) as db:
                    if operation == "foreign_key":
                        db.get(Courier, 1).place_id = "missing"
                    elif operation == "entry_update":
                        db.get(Entry, 1).text = "Changed."
                    else:
                        db.delete(db.get(Entry, 1))

    def test_readonly_session_rejects_writes_and_does_not_create_a_database(self):
        with self.assertRaises(OperationalError):
            with session(self.path, readonly=True) as db:
                db.scalar(select(1))
        self.assertFalse(self.path.exists())
        initialize(self.path, self.now)
        with self.assertRaises(OperationalError):
            with session(self.path, readonly=True) as db:
                db.get(Courier, 1).place_id = "repair-shop"
        self.assertEqual(snapshot(self.path)["courier"]["place_id"], "depot")

    def test_failure_after_writes_rolls_back_the_whole_tick(self):
        initialize(self.path, self.now)
        before = snapshot(self.path)
        with patch("still.simulation._encounter", side_effect=ValueError("interrupted")):
            with self.assertRaises(ValueError):
                tick(self.path, "rollback", self.now, {"kind": "move", "place_id": "repair-shop"})
        self.assertEqual(snapshot(self.path), before)
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Event)), 0)

    def test_concurrent_duplicate_ticks_apply_once(self):
        initialize(self.path, self.now)
        def apply(_):
            return tick(self.path, "same", self.now, {"kind": "move", "place_id": "repair-shop"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, range(2)))
        self.assertEqual(results[0], results[1])
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 1)
            self.assertEqual(db.get(Encounter, "ivo").count, 1)
