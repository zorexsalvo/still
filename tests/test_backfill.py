import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from still.backfill import backfill
from still.clock import timestamp
from still.db import session
from still.models import BackfillTick, Entry, Tick, WorkerState, WorldClock
from still.simulation import tick
from still.viewer import diary, published
from still.worker import prepare
from still.world import snapshot


class BackfillTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "live.sqlite3"
        self.now = datetime.now(UTC)
        self.day = self.now.date() - timedelta(days=1)
        prepare(self.path)
        with session(self.path) as db:
            tick = Tick(tick_key="live", at=timestamp(self.now), action={"kind": "wait"}, hardware={})
            db.add(tick)
            db.flush()
            db.add(Entry(tick_id=tick.id, at=tick.at, text="I waited today."))
        self.before = snapshot(self.path)
        with session(self.path, readonly=True) as db:
            self.clock = db.get(WorldClock, 1).last_seen
            self.due = db.get(WorkerState, 1).next_tick_at

    def generate(self):
        with patch("still.engine.choose", side_effect=lambda url, model, actions, state: actions[0]), patch(
            "still.engine.generate", return_value="I made a note."
        ):
            return backfill(self.path, self.day, "http://unused/v1", "test-model")

    def unchanged(self):
        self.assertEqual(snapshot(self.path), self.before)
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.get(WorldClock, 1).last_seen, self.clock)
            self.assertEqual(db.get(WorkerState, 1).next_tick_at, self.due)
            self.assertEqual(db.get(Entry, 1).text, "I waited today.")

    def test_import_preserves_live_world_labels_history_and_rejects_duplicates(self):
        count = self.generate()
        self.assertGreater(count, 0)
        self.unchanged()
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BackfillTick)), 24)
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 25)
            self.assertEqual(db.scalar(select(func.count()).select_from(Entry)), count + 1)
        rows = published(self.path)
        self.assertEqual(rows[0]["text"], "I waited today.")
        self.assertIsNone(rows[0]["backdated_at"])
        self.assertTrue(all(row["backdated_at"] for row in rows[1:]))
        self.assertTrue(all(datetime.fromisoformat(row["at"]).date() == self.day for row in rows[1:]))
        self.assertIn("not recorded live", diary(self.path).body.decode())
        with self.assertRaisesRegex(ValueError, "backwards"):
            tick(self.path, "backwards-after-import", self.now - timedelta(hours=1), {"kind": "wait"})
        with self.assertRaisesRegex(ValueError, "already been backfilled"):
            self.generate()
        self.unchanged()

    def test_failed_generation_imports_nothing(self):
        with patch("still.backfill.run_tick", return_value={"error": "model offline", "entry": None}):
            with self.assertRaisesRegex(RuntimeError, "no history imported"):
                backfill(self.path, self.day, "http://unused/v1", "test-model")
        self.unchanged()
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(BackfillTick)), 0)

    def test_failed_import_rolls_back_even_previously_inserted_entries(self):
        with session(self.path) as db:
            db.connection().exec_driver_sql("""CREATE TRIGGER reject_backfill BEFORE INSERT ON ticks
                WHEN NEW.tick_key LIKE 'backfill:%:hour-1'
                BEGIN SELECT RAISE(ABORT, 'test import failure'); END""")
        with self.assertRaises(IntegrityError):
            self.generate()
        self.unchanged()
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Entry)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(BackfillTick)), 0)

    def test_current_or_future_day_is_rejected_before_generation(self):
        for day in (self.now.date(), self.now.date() + timedelta(days=1)):
            with self.subTest(day=day), self.assertRaises(ValueError):
                backfill(self.path, day, "http://unused/v1", "test-model")
