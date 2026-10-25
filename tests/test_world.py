import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from still.world import delivery_description, initialize, snapshot


class WorldTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "still.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)

    def test_initial_route_and_delivery_queue(self):
        state = snapshot(self.path)
        self.assertEqual(state["timezone"], "UTC")
        self.assertEqual(state["courier"]["place_id"], "depot")
        self.assertEqual(len(state["places"]), 3)
        self.assertEqual(len(state["people"]), 2)
        self.assertEqual(len(state["deliveries"]), 2)
        self.assertEqual([row["description"] for row in state["deliveries"]],
                         ["corrected directory index", "access request"])
        for delivery in state["deliveries"]:
            self.assertEqual(delivery["status"], "queued")
            self.assertGreater(datetime.fromisoformat(delivery["due_at"]), self.now)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_terminal_delivery_descriptions_support_legacy_and_named_ids(self):
        for identifier, description in {
            "parcel-1": "corrected directory index", "Parcel-2": "access request",
            "parcel-3": "sealed directory copy", "parcel-9": "corrected directory index 9",
            "parcel-10": "access request 10", "parcel-0": "parcel-0",
            "corrected directory index": "corrected directory index",
        }.items():
            with self.subTest(identifier=identifier):
                self.assertEqual(delivery_description(identifier), description)

    def test_restart_preserves_changes_and_does_not_reseed_deliveries(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE courier SET place_id = 'repair-shop' WHERE id = 1")
            connection.execute("UPDATE deliveries SET status = 'delivered' WHERE id = 'parcel-1'")
            connection.execute("DELETE FROM deliveries WHERE id = 'parcel-2'")
        before = snapshot(self.path)
        initialize(self.path, self.now + timedelta(days=1))
        self.assertEqual(snapshot(self.path), before)

    def test_constraints_reject_invalid_delivery(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for statement in [
                "UPDATE deliveries SET status = 'invented' WHERE id = 'parcel-1'",
                "UPDATE deliveries SET destination_id = 'missing' WHERE id = 'parcel-1'",
                "UPDATE deliveries SET destination_id = origin_id WHERE id = 'parcel-1'",
                "UPDATE deliveries SET due_at = created_at WHERE id = 'parcel-1'",
            ]:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
                connection.rollback()
