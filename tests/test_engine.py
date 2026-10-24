import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from still.db import session
from still.engine import choose, run_tick, writing_moment
from still.models import BackfillTick, Entry, Event, Tick
from still.prose import validate
from still.world import initialize, snapshot


class EngineTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "still.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)

    @patch("still.engine.complete")
    def test_choice_schema_and_validation(self, complete):
        actions = [{"kind": "move", "place_id": "repair-shop"}, {"kind": "wait"}]
        complete.return_value = '{"choice":0}'
        self.assertEqual(choose("http://local/v1", "still", actions, {}), actions[0])
        schema = complete.call_args.kwargs["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["choice"]["enum"], [0, 1])
        prompt = json.loads(complete.call_args.args[2][1]["content"])
        self.assertEqual(prompt["actions"], [
            {"choice": 0, "action": actions[0]}, {"choice": 1, "action": actions[1]},
        ])
        for value in [True, -1, 2, "0", None]:
            complete.return_value = json.dumps({"choice": value})
            with self.subTest(value=value), self.assertRaises(ValueError):
                choose("http://local/v1", "still", actions, {})
        for text in ['[]', '{"choice":0,"extra":1}', 'not json']:
            complete.return_value = text
            with self.subTest(text=text), self.assertRaises(ValueError):
                choose("http://local/v1", "still", actions, {})

    @patch("still.engine.generate", return_value="I went to the Repair shop. I met Ivo there.")
    @patch("still.engine.choose", return_value={"kind": "move", "place_id": "repair-shop"})
    def test_publication_is_once_only_and_immutable(self, choose, generate):
        result = run_tick(self.path, "first", "http://local/v1", "still", now=self.now)
        self.assertIsNotNone(result["entry"])
        again = run_tick(self.path, "first", "http://local/v1", "still", now=self.now)
        self.assertEqual(result["entry"], again["entry"])
        self.assertEqual(choose.call_count, 1)
        self.assertEqual(generate.call_count, 1)
        with closing(sqlite3.connect(self.path)) as connection:
            for sql in ["UPDATE entries SET text = 'revised'", "DELETE FROM entries"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)
                connection.rollback()

    @patch("still.engine.generate")
    @patch("still.engine.choose", return_value={"kind": "wait"})
    def test_silent_tick_never_calls_writer(self, choose, generate):
        result = run_tick(self.path, "quiet", "http://local/v1", "still", now=self.now)
        self.assertIsNone(result["entry"])
        generate.assert_not_called()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM publications").fetchone()[0], "silent")

    @patch("still.engine.generate", side_effect=ValueError("truncated output"))
    @patch("still.engine.choose", return_value={"kind": "move", "place_id": "repair-shop"})
    def test_failed_writer_is_not_retried_or_published_later(self, choose, generate):
        result = run_tick(self.path, "bad", "http://local/v1", "still", now=self.now)
        self.assertIsNone(result["entry"])
        self.assertIn("truncated", result["error"])
        self.assertEqual(snapshot(self.path)["courier"]["place_id"], "repair-shop")
        run_tick(self.path, "bad", "http://local/v1", "still", now=self.now)
        self.assertEqual(generate.call_count, 1)

    @patch("still.engine.generate")
    @patch("still.engine.choose", side_effect=URLError("offline"))
    def test_choice_failure_records_a_wait_without_inventing_an_action(self, choose, generate):
        result = run_tick(self.path, "offline", "http://local/v1", "still", now=self.now)
        self.assertIn("offline", result["error"])
        self.assertIsNone(result["entry"])
        self.assertEqual(snapshot(self.path)["courier"]["place_id"], "depot")
        generate.assert_not_called()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(json.loads(connection.execute("SELECT action FROM ticks").fetchone()[0]), {"kind": "wait"})

    @patch("still.engine.generate", side_effect=KeyboardInterrupt)
    @patch("still.engine.choose", return_value={"kind": "move", "place_id": "repair-shop"})
    def test_interrupted_generation_is_not_retried(self, choose, generate):
        with self.assertRaises(KeyboardInterrupt):
            run_tick(self.path, "interrupted", "http://local/v1", "still", now=self.now)
        result = run_tick(self.path, "interrupted", "http://local/v1", "still", now=self.now)
        self.assertIsNone(result["entry"])
        self.assertEqual(generate.call_count, 1)

    @patch("still.engine.generate", return_value="I made a note.")
    @patch("still.engine.choose")
    def test_writer_receives_recent_context_but_not_old_entries_or_backfill(self, choose, generate):
        choose.return_value = {"kind": "move", "place_id": "repair-shop"}
        run_tick(self.path, "meeting", "http://local/v1", "still", now=self.now)
        choose.return_value = {"kind": "collect", "delivery_id": "parcel-1"}
        run_tick(self.path, "pickup", "http://local/v1", "still", now=self.now)
        with session(self.path) as db:
            past = Tick(tick_key="backfill", at="2026-10-23T00:00:00+00:00", action={"kind": "wait"}, hardware={})
            db.add(past)
            db.flush()
            db.add(BackfillTick(tick_id=past.id, day="2026-10-23", generated_at=self.now.isoformat(), model="test"))
            db.add(Event(tick_id=past.id, kind="test", facts={"text": "Backfilled noise."}))
            db.add(Entry(tick_id=past.id, at=past.at, text="I wrote a synthetic old entry."))
        choose.return_value = {"kind": "move", "place_id": "records-office"}
        run_tick(self.path, "arrival", "http://local/v1", "still", now=self.now)
        moment = generate.call_args.args[2]
        self.assertIn("I collected the corrected directory index.", moment)
        self.assertIn("Current moment:\nI went to the Records office.", moment)
        self.assertNotIn("I met Ivo", moment)
        self.assertNotIn("Backfilled noise", moment)

    def test_recent_context_is_bounded_and_chronological(self):
        with session(self.path) as db:
            for number in range(12):
                previous = Tick(tick_key=f"context-{number}", at=self.now.isoformat(), action={"kind": "wait"}, hardware={"temperature_c": 999})
                db.add(previous)
                db.flush()
                db.add(Event(tick_id=previous.id, kind="test", facts={"text": f"I noticed marker {number}."}))
            current = Tick(tick_key="current", at=self.now.isoformat(), action={"kind": "wait"}, hardware={})
            db.add(current)
            db.flush()
            current_id = current.id
        moment = writing_moment(self.path, {"id": current_id, "events": [{"text": "I arrived."}]})
        self.assertEqual(moment.count("I noticed marker"), 8)
        self.assertNotIn("marker 3.", moment)
        self.assertLess(moment.index("marker 4."), moment.index("marker 11."))
        self.assertIn("Current moment:\nI arrived.", moment)
        self.assertNotIn("999", moment)

    def test_voice_checks(self):
        self.assertEqual(validate("I left it on the shelf."), "I left it on the shelf.")
        for text in ["Nothing happened.", "I felt lonely.", "I was like a shadow.",
                     "I went out. I came back. I went out. I came back. I stopped. I waited. I left.",
                     "I wrote this for you.", "```I waited.```"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate(text)
