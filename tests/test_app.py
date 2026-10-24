import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from still import app, health, lifespan, main, world
from still.db import session
from still.models import Downtime, Tick, WorldClock
from still.worker import prepare


class AppTest(unittest.TestCase):
    def test_viewer_restart_does_not_change_world_or_worker_clock(self):
        async def check(path):
            with session(path, readonly=True) as db:
                before = db.get(WorldClock, 1).last_seen
            for _ in range(2):
                async with lifespan(app):
                    self.assertEqual(health(), {"status": "ok"})
                    self.assertEqual(world()["courier"]["place_id"], "depot")
                    self.assertEqual(len(world()["deliveries"]), 2)
            with session(path, readonly=True) as db:
                self.assertEqual(db.get(WorldClock, 1).last_seen, before)
                self.assertIsNone(db.scalar(select(Tick)))
                self.assertIsNone(db.scalar(select(Downtime)))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "still.sqlite3"
            with patch.dict(os.environ, {"STILL_DB_PATH": str(path)}):
                with patch("still.worker.utcnow", return_value=datetime(2026, 10, 24, 12, tzinfo=UTC)):
                    prepare(path)
                asyncio.run(check(path))

    @patch("still.uvicorn.run")
    def test_native_viewer_binds_all_interfaces(self, run):
        main()
        run.assert_called_once_with("still:app", host="0.0.0.0", port=8000)

    def test_viewer_does_not_initialize_a_missing_database(self):
        async def check():
            async with lifespan(app):
                self.fail("viewer must not create the database")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.sqlite3"
            with patch.dict(os.environ, {"STILL_DB_PATH": str(path)}):
                with self.assertRaises(OperationalError):
                    asyncio.run(check())
            self.assertFalse(path.exists())
