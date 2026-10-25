import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from still import app, world, world_view
from still.db import session
from still.models import Arrival, BackfillTick, Delivery, Entry, Event, PersonalFile, PersonalLife, Person, Place, Relationship, Tick, WorldClock
from still.worker import prepare
from still.world import initialize, snapshot
from still.viewer import diary
from still.world_view import world_page


class WorldViewTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "world.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)

    def test_world_contains_real_state_and_readonly_navigation(self):
        body = world_page(self.path, now=self.now).body.decode()
        for text in ("Courier", "Personal file", "Places and people", "Deliveries", "Recent changes",
                     "old-index", "corrected directory index", "access request", "All times UTC"):
            self.assertIn(text, body)
        self.assertNotIn("parcel-", body)
        self.assertIn('href="/world/view" aria-current="page"', body)
        self.assertIn('href="/"', body)
        self.assertIn("never advances it", body)
        self.assertNotIn("<button", body)

    def test_borrowed_notes_threads_and_relationship_memory_are_visible_and_escaped(self):
        with session(self.path) as db:
            life = db.get(PersonalLife, 1)
            life.owner_stage = 4
            life.borrowed_from = 'ivo'
            life.borrowed_at = self.now.isoformat()
            life.return_due_at = (self.now + timedelta(days=1)).isoformat()
            life.read_during_loan = 1
            life.reading_note = '<script>private note</script>'
            memory = db.get(Relationship, 'ivo')
            memory.loans, memory.returns, memory.late_returns = 2, 1, 1
        before = snapshot(self.path)
        response = world_page(self.path, now=self.now)
        body = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Borrowed notes', body)
        self.assertIn('repair-notes', body)
        self.assertIn('Open threads', body)
        self.assertIn('Notes returned: 1; late: 1.', body)
        self.assertIn('&lt;script&gt;private note&lt;/script&gt;', body)
        self.assertNotIn('<script>', body)
        self.assertEqual(snapshot(self.path), before)

    def test_host_readings_blend_into_the_footer_in_life_terms(self):
        with session(self.path) as db:
            db.add(Tick(tick_key="reading", at=self.now.isoformat(), action={"kind": "wait"},
                        hardware={"temperature_c": 47.5, "model_endpoint_ms": 12.3,
                                  "sampled_at": self.now.isoformat()}))
        body = world_page(self.path, now=self.now).body.decode()
        self.assertIn("Warmth", body)
        self.assertIn("47.5", body)
        self.assertIn("Reply", body)
        self.assertIn("12.3", body)
        # The literal reading is present, but quietly subordinate.
        self.assertIn("CPU temp", body)
        self.assertIn(">model<", body)
        # It is a footer meta row, not a standalone world section.
        self.assertGreater(body.index("Warmth"), body.index("<footer"))
        self.assertNotIn("<h3>Host</h3>", body)

    def test_host_readings_absent_leave_no_footer_noise(self):
        body = world_page(self.path, now=self.now).body.decode()
        self.assertNotIn("Warmth", body)
        self.assertNotIn("Reply", body)

    def test_host_readings_also_appear_in_the_diary_footer(self):
        with session(self.path) as db:
            db.add(Tick(tick_key="reading", at=self.now.isoformat(), action={"kind": "wait"},
                        hardware={"temperature_c": 41.0, "model_endpoint_ms": 9.5,
                                  "sampled_at": self.now.isoformat()}))
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn("Warmth", body)
        self.assertIn("41.0", body)
        self.assertGreater(body.index("Warmth"), body.index("<footer"))

    def test_empty_deliveries_and_activity_remain_readable(self):
        with session(self.path) as db:
            for delivery in db.scalars(select(Delivery)):
                db.delete(delivery)
        response = world_page(self.path, now=self.now)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No deliveries recorded.", response.body.decode())
        self.assertIn("No changes recorded yet.", response.body.decode())

    def test_html_route_does_not_replace_the_existing_json_api(self):
        with patch.object(app.state, "db_path", self.path, create=True):
            self.assertEqual(world()["courier"]["place_id"], "depot")
            response = world_view()
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])

    def test_all_stored_text_is_escaped(self):
        with session(self.path) as db:
            db.get(Place, "depot").name = '<script>alert("place")</script>'
            db.get(Person, "ivo").name = '<script>alert("person")</script>'
            db.get(PersonalFile, 1).contents = '<img src=x onerror="alert(1)">'
            tick = Tick(tick_key="fixture", at=self.now.isoformat(), action={"kind": "wait"}, hardware={})
            db.add(tick)
            db.flush()
            db.add(Event(tick_id=tick.id, kind="test", facts={"text": '<script>alert("event")</script>'}))
        response = world_page(self.path, now=self.now)
        body = response.body.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>", body)
        self.assertNotIn("<img", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("&lt;img", body)
        self.assertIn("default-src 'none'", response.headers["content-security-policy"])

    def test_occasional_people_and_backdated_activity_are_labelled(self):
        with session(self.path) as db:
            db.add(Person(id="inspector-ren", name="Ren", role="inspector", place_id="records-office"))
            tick = Tick(tick_key="history", at=self.now.isoformat(), action={"kind": "wait"}, hardware={})
            db.add(tick)
            db.flush()
            db.add(Arrival(tick_id=tick.id, person_id="inspector-ren"))
            db.add(BackfillTick(tick_id=tick.id, day="2026-10-24", generated_at=self.now.isoformat(), model="test"))
            db.add(Event(tick_id=tick.id, kind="inspection", facts={"text": "Ren checked the receipt."}))
        body = world_page(self.path, now=self.now).body.decode()
        self.assertIn("Occasional visitors", body)
        self.assertIn("Ren · inspector. Not permanent staff.", body)
        self.assertNotIn("<strong>Ren</strong>", body)
        self.assertIn("not recorded live", body)

    @patch("still.engine.choose", side_effect=ConnectionError("model unavailable"))
    @patch("still.engine.generate", side_effect=ConnectionError("model unavailable"))
    def test_reads_never_advance_state_or_request_inference(self, generate, choose):
        before = snapshot(self.path)
        for _ in range(3):
            self.assertEqual(world_page(self.path, now=self.now).status_code, 200)
        self.assertEqual(snapshot(self.path), before)
        generate.assert_not_called()
        choose.assert_not_called()
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Tick)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Entry)), 0)

    def test_stale_worker_is_not_presented_as_live(self):
        with patch("still.worker.utcnow", return_value=self.now):
            prepare(self.path)
        self.assertNotIn("not checked in recently", world_page(self.path, now=self.now).body.decode())
        body = world_page(self.path, now=self.now + timedelta(seconds=91)).body.decode()
        self.assertIn("not checked in recently", body)
        with session(self.path, readonly=True) as db:
            self.assertEqual(datetime.fromisoformat(db.get(WorldClock, 1).last_seen), self.now)

    def test_missing_database_has_a_recoverable_error_without_creating_it(self):
        missing = self.path.parent / "absent.sqlite3"
        with self.assertLogs("still.world_view", level="ERROR"):
            response = world_page(missing, now=self.now)
        self.assertEqual(response.status_code, 503)
        self.assertIn("The world could not be loaded", response.body.decode())
        self.assertIn('href="/world/view">Try again', response.body.decode())
        self.assertNotIn(str(missing), response.body.decode())
        self.assertFalse(missing.exists())
