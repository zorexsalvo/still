import asyncio
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from still.db import session
from still.models import Entry, Publication, Tick, WorkerLease, WorkerRun, WorkerState
from still.simulation import tick
from still.worker import claim, healthy, next_tick, prepare, release, renew, run_once


class WorkerDatabaseTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "worker.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        with patch("still.worker.utcnow", return_value=self.now):
            prepare(self.path)

    @patch("still.worker.readings", return_value={})
    @patch("still.worker.run_tick", return_value={"error": None})
    def test_run_round_trip_through_orm(self, run_tick, readings):
        finished = self.now + timedelta(seconds=90)
        with patch("still.worker.utcnow", side_effect=[self.now, finished]):
            self.assertTrue(asyncio.run(run_once(self.path, "http://local/v1", "still")))
        self.assertEqual(next_tick(self.path), finished + timedelta(minutes=10))
        with session(self.path, readonly=True) as db:
            run = db.scalar(select(WorkerRun))
            self.assertEqual(run.finished_at, finished.isoformat(timespec="microseconds"))
            self.assertIsNone(db.get(WorkerState, 1).active_tick_key)
        with patch("still.worker.utcnow", return_value=finished):
            self.assertFalse(asyncio.run(run_once(self.path, "http://local/v1", "still")))
        self.assertEqual(run_tick.call_count, 1)

    @patch("still.worker.readings", return_value={})
    @patch("still.worker.run_tick", return_value={"error": None})
    def test_restart_preserves_rest_and_long_gap_does_not_backfill(self, run_tick, readings):
        with patch("still.worker.utcnow", return_value=self.now):
            asyncio.run(run_once(self.path, "http://local/v1", "still"))
        due = next_tick(self.path)
        with patch("still.worker.utcnow", return_value=self.now + timedelta(minutes=5)):
            prepare(self.path)
            self.assertEqual(next_tick(self.path), due)
            self.assertFalse(asyncio.run(run_once(self.path, "http://local/v1", "still")))
        later = self.now + timedelta(hours=5)
        with patch("still.worker.utcnow", return_value=later):
            prepare(self.path)
            self.assertTrue(asyncio.run(run_once(self.path, "http://local/v1", "still")))
        self.assertEqual(run_tick.call_count, 2)
        self.assertEqual(next_tick(self.path), later + timedelta(minutes=10))

    def test_crash_before_simulation_does_not_invent_an_action(self):
        with session(self.path) as db:
            db.add(WorkerRun(tick_key="before-choice", started_at=self.now.isoformat()))
            db.flush()
            db.get(WorkerState, 1).active_tick_key = "before-choice"
        later = self.now + timedelta(hours=10)
        with patch("still.worker.utcnow", return_value=later):
            prepare(self.path)
        with session(self.path, readonly=True) as db:
            self.assertIsNone(db.scalar(select(Tick)))
            self.assertIsNone(db.scalar(select(Entry)))
            self.assertIn("interrupted", db.get(WorkerRun, "before-choice").error)
        self.assertEqual(next_tick(self.path), later + timedelta(minutes=10))

    def test_health_is_fresh_and_never_creates_a_missing_database(self):
        with patch("still.worker.utcnow", return_value=self.now):
            self.assertTrue(healthy(self.path))
            missing = self.path.parent / "missing.sqlite3"
            self.assertFalse(healthy(missing))
            self.assertFalse(missing.exists())
        with patch("still.worker.utcnow", return_value=self.now + timedelta(seconds=91)):
            self.assertFalse(healthy(self.path))

    def test_database_lease_rejects_another_owner_and_fences_stale_workers(self):
        with patch("still.worker.utcnow", return_value=self.now):
            claim(self.path, "first")
            with self.assertRaises(RuntimeError):
                claim(self.path, "second")
        release(self.path, "not-the-owner")
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.get(WorkerLease, 1).owner, "first")
        later = self.now + timedelta(seconds=91)
        with patch("still.worker.utcnow", return_value=later):
            claim(self.path, "second")
            with self.assertRaises(RuntimeError):
                renew(self.path, "first")
        with self.assertRaises(RuntimeError):
            tick(self.path, "stale", later, {"kind": "move", "place_id": "repair-shop"}, owner="first")
        with session(self.path, readonly=True) as db:
            self.assertIsNone(db.scalar(select(Tick)))
        release(self.path, "second")
        with session(self.path, readonly=True) as db:
            self.assertIsNone(db.get(WorkerLease, 1))

    def test_second_worker_is_rejected_before_initialization(self):
        with (self.path.parent / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(
                [sys.executable, "-m", "still.worker"],
                env={**os.environ, "STILL_DB_PATH": str(self.path)},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Another Still worker", result.stderr)

    def test_recovery_updates_orm_records_without_republishing(self):
        result = tick(self.path, "interrupted", self.now, {"kind": "move", "place_id": "repair-shop"})
        with session(self.path) as db:
            db.add(Publication(tick_id=result["id"], status="writing"))
            db.add(WorkerRun(tick_key="interrupted", started_at=self.now.isoformat()))
            db.flush()
            db.get(WorkerState, 1).active_tick_key = "interrupted"
        resumed = self.now + timedelta(hours=2)
        with patch("still.worker.utcnow", return_value=resumed):
            prepare(self.path)
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.get(Publication, result["id"]).status, "failed")
            self.assertIn("interrupted", db.get(WorkerRun, "interrupted").error)
            self.assertIsNone(db.get(WorkerState, 1).active_tick_key)
        self.assertEqual(next_tick(self.path), resumed + timedelta(minutes=10))
