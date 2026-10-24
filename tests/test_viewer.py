import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from still.db import session
from still.models import Entry, Tick
from still.viewer import PAGE_SIZE, diary, published
from still.world import initialize


class ViewerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "diary.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)

    def add(self, text="I waited.", *, at=None):
        timestamp = (at or self.now).isoformat()
        with session(self.path) as db:
            tick = Tick(tick_key=text + timestamp, at=timestamp, action={"kind": "wait"}, hardware={})
            db.add(tick)
            db.flush()
            db.add(Entry(tick_id=tick.id, at=timestamp, text=text))

    def test_only_published_text_is_rendered_and_escaped(self):
        self.add('I found <script>alert("x")</script> on a label.')
        response = diary(self.path, now=self.now)
        body = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertIn("All times UTC", body)
        self.assertIn('datetime="2026-10-24T12:00:00+00:00"', body)
        self.assertNotIn("parcel-1", body)
        self.assertNotIn("repair-shop", body)
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])
        self.assertIn('href="#entry-1"', body)

    def test_empty_state_does_not_create_content(self):
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn("No entries yet.", body)
        self.assertNotIn('class="entry"', body)
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Entry)), 0)

    def test_pagination_does_not_repeat_entries(self):
        for number in range(PAGE_SIZE + 2):
            self.add(f"I carried package {number}.", at=self.now + timedelta(seconds=number))
        first = published(self.path)
        self.assertEqual(len(first), PAGE_SIZE + 1)
        cursor = first[PAGE_SIZE - 1]["id"]
        first_ids = {entry["id"] for entry in first[:PAGE_SIZE]}
        second = published(self.path, cursor)
        self.assertFalse(first_ids & {entry["id"] for entry in second})
        self.assertEqual(len(first_ids) + len(second), PAGE_SIZE + 2)
        self.assertIn(f'href="/?before={cursor}"', diary(self.path, now=self.now).body.decode())
        self.assertIn("Earlier entries", diary(self.path, cursor, now=self.now).body.decode())

    def test_late_inserted_history_uses_time_order_and_stable_cursor_ties(self):
        self.add("I wrote the first live entry.")
        self.add("I wrote yesterday's entry.", at=self.now - timedelta(days=1))
        self.add("I wrote another live entry.")
        self.assertEqual([row["id"] for row in published(self.path)], [3, 1, 2])
        self.assertEqual([row["id"] for row in published(self.path, 3)], [1, 2])
        self.assertEqual([row["id"] for row in published(self.path, 1)], [2])
        self.assertEqual(published(self.path, 99999), [])

    def test_only_complete_blank_utc_dates_are_counted(self):
        self.add("I met Ivo.", at=self.now - timedelta(days=4))
        self.add("I went back.", at=self.now - timedelta(days=1))
        body = diary(self.path, now=self.now).body.decode()
        self.assertEqual(body.count("2 full UTC days without an entry."), 1)
        self.assertNotIn("1 full UTC day", body)

    def test_database_error_has_an_honest_recoverable_state(self):
        missing = self.path.parent / "missing.sqlite3"
        with self.assertLogs("still.viewer", level="ERROR"):
            response = diary(missing, now=self.now)
        self.assertEqual(response.status_code, 503)
        self.assertIn("The diary could not be loaded.", response.body.decode())
        self.assertIn('href="/">Try again', response.body.decode())
        self.assertNotIn(str(missing), response.body.decode())
        self.assertFalse(missing.exists())

    @patch("still.engine.choose", side_effect=ConnectionError("model offline"))
    @patch("still.engine.generate", side_effect=ConnectionError("model offline"))
    def test_reading_does_not_call_the_model_or_advance_the_sim(self, generate, choose):
        self.add("I left it on the shelf.")
        before = published(self.path)
        for _ in range(3):
            response = diary(self.path, now=self.now)
            self.assertEqual(response.status_code, 200)
            self.assertIn("I left it on the shelf.", response.body.decode())
        choose.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(published(self.path), before)
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 1)
