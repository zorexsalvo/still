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
from still.engine import run_tick
from still.models import BackfillTick, Entry, Event, Tick, WorkerState, WorldClock
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

    def generate(self, *, day=None, days=1):
        with patch("still.engine.choose", side_effect=lambda url, model, actions, state: actions[0]), patch(
            "still.engine.generate", return_value="I made a note."
        ):
            return backfill(self.path, day or self.day, "http://unused/v1", "test-model", days=days)

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

    def test_two_days_are_continuous_and_tagged_by_their_own_utc_dates(self):
        first = self.day - timedelta(days=1)
        self.generate(day=first, days=2)
        self.unchanged()
        with session(self.path, readonly=True) as db:
            counts = dict(db.execute(select(BackfillTick.day, func.count()).group_by(BackfillTick.day)).all())
            self.assertEqual(counts, {first.isoformat(): 24, self.day.isoformat(): 24})
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 49)
            meetings = list(db.execute(select(Event.facts, BackfillTick.day).join(
                BackfillTick, Event.tick_id == BackfillTick.tick_id
            ).where(Event.kind == "first_meeting")))
            self.assertEqual(sum(facts["person_id"] == "mara" for facts, _ in meetings), 1)
            self.assertEqual(sum(facts["person_id"] == "ivo" for facts, _ in meetings), 1)
            self.assertTrue(all(day == first.isoformat() for _, day in meetings))
        for day in (first, self.day):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "already been backfilled"):
                self.generate(day=day)

    def test_failure_on_second_day_imports_neither_day(self):
        calls = 0
        def fail_on_second_day(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 25:
                return {"error": "model offline", "entry": None}
            return run_tick(*args, **kwargs)
        with patch("still.backfill.run_tick", side_effect=fail_on_second_day):
            with self.assertRaisesRegex(RuntimeError, "no history imported"):
                self.generate(day=self.day - timedelta(days=1), days=2)
        self.unchanged()
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(BackfillTick)), 0)

    def test_invalid_day_counts_or_unfinished_ranges_are_rejected(self):
        for days in (0, -1, True, 2):
            with self.subTest(days=days), self.assertRaises(ValueError):
                backfill(self.path, self.day, "http://unused/v1", "test-model", days=days)

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
