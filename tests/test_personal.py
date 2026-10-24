import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from still.db import session
from still.models import Courier, Delivery, PersonalFile
from still.simulation import options, tick
from still.worker import prepare
from still.world import initialize, snapshot


class PersonalLifeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "world.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)
        self.number = 0

    def act(self, action, hours=0):
        self.number += 1
        return tick(self.path, str(self.number), self.now + timedelta(hours=hours), action)

    def test_recovery_is_persistent_and_replay_does_not_advance_it(self):
        result = self.act({"kind": "restore_file"})
        personal = snapshot(self.path)["personal_file"]
        self.assertEqual(personal["progress"], 1)
        self.assertIn("path: /archive/landing", personal["contents"])
        self.assertIn("owner: [unreadable]", personal["contents"])
        self.assertTrue(result["publish"])
        self.assertEqual(result["events"][0]["kind"], "file_recovered")
        replay = tick(self.path, "1", self.now + timedelta(hours=1), {"kind": "restore_file"})
        self.assertEqual(replay, result)
        initialize(self.path, self.now + timedelta(days=1))
        self.assertEqual(snapshot(self.path)["personal_file"], personal)

    def test_completed_file_cannot_be_repaired_again_or_jumped_ahead(self):
        with self.assertRaises(ValueError):
            self.act({"kind": "restore_file", "progress": 3})
        for hour in range(3):
            result = self.act({"kind": "restore_file"}, hour)
        personal = snapshot(self.path)["personal_file"]
        self.assertEqual(personal["progress"], 3)
        self.assertIn("owner: [blank]", personal["contents"])
        self.assertIn("keep: yes", personal["contents"])
        self.assertIn("file_completed", [event["kind"] for event in result["events"]])
        self.assertNotIn({"kind": "restore_file"}, options(self.path))
        with self.assertRaises(ValueError):
            self.act({"kind": "restore_file"}, 3)
        self.assertEqual(snapshot(self.path)["personal_file"], personal)

    def test_nonwork_visits_react_to_progress_and_cannot_loop(self):
        visit = {"kind": "visit", "person_id": "ivo"}
        first = self.act(visit)
        self.assertTrue(first["publish"])
        self.assertIn("damaged lines", " ".join(event["text"] for event in first["events"]))
        self.assertEqual(snapshot(self.path)["courier"]["place_id"], "repair-shop")
        self.assertNotIn(visit, options(self.path))
        with self.assertRaises(ValueError):
            self.act(visit, 1)
        self.act({"kind": "restore_file"}, 1)
        self.assertIn(visit, options(self.path))
        second = self.act(visit, 2)
        self.assertIn("could not find the path", " ".join(event["text"] for event in second["events"]))
        self.assertEqual(snapshot(self.path)["personal_file"]["shown_progress"], 1)
        self.assertNotIn(visit, options(self.path))

    def test_a_personal_choice_can_make_pending_work_late(self):
        self.act({"kind": "restore_file"})
        self.act({"kind": "visit", "person_id": "ivo"}, 3)
        result = self.act({"kind": "restore_file"}, 5)
        self.assertIn("file_recovered", [event["kind"] for event in result["events"]])
        self.assertIn("deadline_missed", [event["kind"] for event in result["events"]])
        with session(self.path, readonly=True) as db:
            self.assertEqual(db.get(Delivery, "parcel-1").status, "queued")
        self.assertEqual(snapshot(self.path)["personal_file"]["progress"], 2)

    def test_hardware_pressure_also_prevents_personal_work(self):
        hardware = {"load_1m": 20, "cpu_count": 1}
        self.assertEqual(options(self.path, hardware), [{"kind": "wait"}])
        with self.assertRaises(ValueError):
            tick(self.path, "too-hot", self.now, {"kind": "restore_file"}, hardware)
        self.assertEqual(snapshot(self.path)["personal_file"]["progress"], 0)

    def test_finished_round_leaves_free_time_and_work_returns_next_day(self):
        for hour in range(7):
            work = [action for action in options(self.path) if action["kind"] in ("move", "collect", "deliver")]
            self.act(work[0], hour)
        state = snapshot(self.path)
        self.assertEqual(len(state["deliveries"]), 2)
        self.assertTrue(all(delivery["status"] == "delivered" for delivery in state["deliveries"]))
        self.assertIn({"kind": "restore_file"}, options(self.path))
        self.act({"kind": "wait"}, 10)
        self.assertEqual(len(snapshot(self.path)["deliveries"]), 2)
        result = self.act({"kind": "wait"}, 12)  # Next UTC midnight, not a forced sleep window.
        self.assertIn("work_arrived", [event["kind"] for event in result["events"]])
        self.assertEqual(len(snapshot(self.path)["deliveries"]), 3)
        self.act({"kind": "wait"}, 13)
        self.assertEqual(len(snapshot(self.path)["deliveries"]), 3)

    def test_existing_world_gets_project_without_resetting_the_courier(self):
        with session(self.path) as db:
            db.delete(db.get(PersonalFile, 1))
            db.get(Courier, 1).place_id = "repair-shop"
        initialize(self.path, self.now + timedelta(days=1))
        state = snapshot(self.path)
        self.assertEqual(state["courier"]["place_id"], "repair-shop")
        self.assertEqual(len(state["deliveries"]), 2)
        self.assertEqual(state["personal_file"]["progress"], 0)

    def test_downtime_does_not_recover_the_file_or_visit_anyone(self):
        with patch("still.worker.utcnow", return_value=self.now):
            prepare(self.path)
        self.act({"kind": "restore_file"})
        before = snapshot(self.path)
        with patch("still.worker.utcnow", return_value=self.now + timedelta(days=2)):
            prepare(self.path)
        self.assertEqual(snapshot(self.path), before)
