import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from still.db import session
from still.models import BackfillTick, Courier, Entry, PersonalFile, PersonalLife, Person, Place, Relationship, Tick, WorkerRun, WorkerState, WorldClock
from still.simulation import tick
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
        self.assertIn("img-src 'self'", response.headers["content-security-policy"])
        self.assertIn('href="/static/favicon.svg"', body)
        self.assertIn('href="/static/favicon.ico"', body)
        self.assertIn('href="#entry-1"', body)

    def test_presence_reports_saved_location_and_real_live_progress_not_inference_health(self):
        with session(self.path) as db:
            db.get(Courier, 1).place_id = 'repair-shop'
            db.add(WorldClock(id=1, last_seen=self.now.isoformat()))
            db.add(WorkerState(id=1, next_tick_at=(self.now + timedelta(hours=1)).isoformat()))
            db.add(Tick(tick_key='success', at=(self.now-timedelta(hours=1)).isoformat(), action={'kind':'wait'}, hardware={}))
            db.add_all([
                WorkerRun(tick_key='success', started_at=(self.now-timedelta(hours=2)).isoformat(),
                          finished_at=(self.now-timedelta(hours=1)).isoformat()),
                WorkerRun(tick_key='failed', started_at=(self.now-timedelta(minutes=30)).isoformat(),
                          finished_at=(self.now-timedelta(minutes=29)).isoformat(), error='SECRET model URL'),
            ])
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('presence-repair', body)
        self.assertIn('At Repair shop', body)
        self.assertIn('Worker check-in recent', body)
        self.assertIn('Last completed live tick: <time datetime="2026-10-24T11:00:00+00:00"', body)
        self.assertIn('Next eligible tick: <time datetime="2026-10-24T13:00:00+00:00"', body)
        self.assertIn('Last finished attempt had an error', body)
        self.assertNotIn('SECRET', body)
        self.assertIn('aria-hidden="true"', body)
        self.assertNotIn('<script', body)
        with session(self.path) as db:
            db.get(WorkerRun, 'failed').error = ''
        self.assertIn('Last finished attempt had an error', diary(self.path, now=self.now).body.decode())

    def test_no_live_run_is_not_mistaken_for_a_completed_replay_tick(self):
        self.add('I wrote a backdated note.', at=self.now - timedelta(days=1))
        with session(self.path) as db:
            db.add(BackfillTick(tick_id=1, day='2026-10-23', generated_at=self.now.isoformat(), model='test'))
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Last completed live tick: none yet', body)
        self.assertIn('No worker check-in yet', body)
        self.assertIn('Next eligible tick: not scheduled', body)

    def test_finished_run_without_a_persisted_tick_is_not_completion_evidence(self):
        with session(self.path) as db:
            db.add(WorkerRun(tick_key='orphan', started_at=self.now.isoformat(), finished_at=self.now.isoformat()))
        self.assertIn('Last completed live tick: none yet', diary(self.path, now=self.now).body.decode())

    def test_presence_distinguishes_stale_and_future_checkins(self):
        with session(self.path) as db:
            db.add(WorldClock(id=1, last_seen=(self.now-timedelta(seconds=91)).isoformat()))
        self.assertIn('Worker check-in stale', diary(self.path, now=self.now).body.decode())
        with session(self.path) as db:
            db.get(WorldClock, 1).last_seen = (self.now+timedelta(seconds=1)).isoformat()
        self.assertIn('Worker check-in recent', diary(self.path, now=self.now).body.decode())
        with session(self.path) as db:
            db.get(WorldClock, 1).last_seen = (self.now+timedelta(minutes=1)).isoformat()
        self.assertIn('Worker check-in time is in the future', diary(self.path, now=self.now).body.decode())

    def test_in_progress_is_not_reported_as_completed(self):
        with session(self.path) as db:
            db.add(WorkerRun(tick_key='active', started_at=self.now.isoformat()))
            db.flush()
            db.add(WorkerState(id=1, next_tick_at=self.now.isoformat(), active_tick_key='active'))
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Tick marked in progress', body)
        self.assertIn('Last completed live tick: none yet', body)
        self.assertNotIn('Next eligible tick:', body)

    def test_presence_escapes_location_and_does_not_use_it_as_a_css_class(self):
        with session(self.path) as db:
            db.get(Place, 'depot').name = '<img src=x onerror=alert(1)>'
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('At &lt;img', body)
        self.assertNotIn('<img', body)
        self.assertIn('presence-depot', body)

    def test_unavailable_status_does_not_hide_readable_diary(self):
        self.add('I kept the owner line blank.')
        with session(self.path) as db:
            WorkerRun.__table__.drop(db.connection())
        with self.assertLogs('still.viewer', level='WARNING'):
            response = diary(self.path, now=self.now)
        self.assertEqual(response.status_code, 200)
        self.assertIn('World status unavailable', response.body.decode())
        self.assertIn('I kept the owner line blank.', response.body.decode())
        self.assertNotIn('<svg', response.body.decode())

    def test_scene_changes_between_recorded_arrival_restoration_and_waiting(self):
        tick(self.path, 'scene-move', self.now, {'kind':'move', 'place_id':'repair-shop'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('scene-walking', body)
        self.assertIn('Still arriving', body)
        self.assertNotIn('class="scene-companion"', body)
        tick(self.path, 'scene-restore', self.now + timedelta(hours=1), {'kind':'restore_file'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('scene-reading', body)
        self.assertIn('Still restoring the file', body)
        # The satchel and the held page are ground-filled so they cover the sleeve and strap
        # strokes behind them instead of braiding with them.
        self.assertEqual(2, body.count('class="scene-solid"'))
        tick(self.path, 'scene-wait', self.now + timedelta(hours=2), {'kind':'wait'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertEqual(1, body.count('class="scene-solid"'))
        self.assertIn('Still waiting', body)

    def test_reading_scene_does_not_invent_the_lenders_presence(self):
        with session(self.path) as db:
            personal = db.get(PersonalFile, 1)
            personal.progress = personal.shown_progress = 3
            life = db.get(PersonalLife, 1)
            life.owner_stage = 4
            life.borrowed_from = 'ivo'
            life.borrowed_at = (self.now-timedelta(hours=1)).isoformat()
            life.return_due_at = (self.now+timedelta(hours=23)).isoformat()
            db.get(Relationship, 'ivo').loans = 1
        tick(self.path, 'scene-read', self.now, {'kind':'read_notes'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still reading', body)
        self.assertIn('scene-reading', body)
        self.assertNotIn('class="scene-companion"', body)

    def test_scene_shows_only_the_latest_recorded_interaction_and_escapes_names(self):
        tick(self.path, 'scene-visit', self.now, {'kind':'visit', 'person_id':'ivo'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still with Ivo', body)
        self.assertIn('class="scene-companion"', body)
        with session(self.path) as db:
            db.get(Person, 'ivo').name = '<img src=x onerror=alert(1)>'
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still with &lt;img', body)
        self.assertNotIn('<img', body)
        tick(self.path, 'scene-after-visit', self.now + timedelta(hours=1), {'kind':'wait'})
        with session(self.path) as db:
            db.add(Tick(tick_key='older-import', at=(self.now-timedelta(days=1)).isoformat(),
                        action={'kind':'visit', 'person_id':'ivo'}, hardware={}))
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still waiting', body)
        self.assertNotIn('class="scene-companion"', body)

    def test_inspector_scene_does_not_depict_handing_the_parcel_to_the_inspector(self):
        for hour, action in (
            (0, {'kind':'move', 'place_id':'repair-shop'}),
            (1, {'kind':'collect', 'delivery_id':'parcel-1'}),
            (2, {'kind':'move', 'place_id':'records-office'}),
            (5, {'kind':'deliver', 'delivery_id':'parcel-1'}),
        ):
            tick(self.path, f'inspector-{hour}', self.now+timedelta(hours=hour), action)
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still with Ren', body)
        self.assertNotIn('class="scene-handoff"', body)
        self.assertIn('recorded moment', body)

    def test_refused_loan_scene_does_not_show_a_successful_exchange(self):
        with session(self.path) as db:
            personal = db.get(PersonalFile, 1)
            personal.progress = personal.shown_progress = 3
            db.get(PersonalLife, 1).owner_stage = 4
            memory = db.get(Relationship, 'ivo')
            memory.loans = memory.returns = memory.late_returns = 1
            memory.needs_help = 1
        tick(self.path, 'scene-refusal', self.now, {'kind':'borrow_notes', 'person_id':'ivo'})
        body = diary(self.path, now=self.now).body.decode()
        self.assertIn('Still with Ivo · request refused', body)
        self.assertIn('class="scene-companion"', body)
        self.assertNotIn('class="scene-handoff"', body)

    def test_scene_precedes_the_diary_but_diagnostics_stay_in_the_footer(self):
        self.add('I kept the owner line blank.')
        body = diary(self.path, now=self.now).body.decode()
        self.assertLess(body.index('class="ambient-world"'), body.index('<main'))
        self.assertGreater(body.index('class="life-status"'), body.index('<footer'))

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
