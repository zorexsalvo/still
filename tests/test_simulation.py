import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from still.simulation import options, tick
from still.world import initialize, snapshot


class SimulationTest(unittest.TestCase):
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

    def test_legal_route_and_quiet_ticks(self):
        self.assertNotIn({"kind": "collect", "delivery_id": "parcel-1"}, options(self.path))
        moved = self.act({"kind": "move", "place_id": "repair-shop"})
        self.assertTrue(moved["publish"])
        collected = self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.assertFalse(collected["publish"])
        self.assertNotIn({"kind": "collect", "delivery_id": "parcel-2"}, options(self.path))
        self.act({"kind": "move", "place_id": "records-office"}, 2)
        delivered = self.act({"kind": "deliver", "delivery_id": "parcel-1"}, 3)
        self.assertFalse(delivered["publish"])
        self.assertEqual(snapshot(self.path)["deliveries"][0]["status"], "delivered")

    def test_courier_cannot_leave_a_pickup_to_bounce_to_another_stop(self):
        self.act({"kind": "move", "place_id": "repair-shop"})
        self.assertEqual(options(self.path), [
            {"kind": "collect", "delivery_id": "parcel-1"}, {"kind": "wait"},
        ])
        before = snapshot(self.path)
        with self.assertRaisesRegex(ValueError, "illegal"):
            self.act({"kind": "move", "place_id": "records-office"}, 1)
        self.assertEqual(snapshot(self.path), before)
        self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.assertEqual(options(self.path), [
            {"kind": "move", "place_id": "records-office"}, {"kind": "wait"},
        ])

    def test_move_biased_choices_still_complete_deliveries_over_a_day(self):
        completed = []
        for hour in range(24):
            actions = options(self.path)
            # Reproduce the model's old travel preference, without letting it wait forever.
            action = next((action for action in actions if action["kind"] == "move"), actions[0])
            result = self.act(action, hour)
            completed.extend(event["delivery_id"] for event in result["events"] if event["kind"] == "delivered")
        self.assertGreaterEqual(len(completed), 6)
        self.assertIn("parcel-1", completed)
        self.assertIn("parcel-2", completed)

    def test_illegal_action_has_no_effect(self):
        before = snapshot(self.path)
        for action in [
            {"kind": "deliver", "delivery_id": "parcel-1"},
            {"kind": "move", "place_id": "missing"},
            {"kind": "wait", "invented": True},
        ]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.act(action)
        self.assertEqual(snapshot(self.path), before)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ticks").fetchone()[0], 0)

    def test_duplicate_tick_is_idempotent_and_clock_cannot_reverse(self):
        result = tick(self.path, "same", self.now, {"kind": "move", "place_id": "repair-shop"})
        again = tick(self.path, "same", self.now + timedelta(hours=1), {"kind": "wait"})
        self.assertEqual(result, again)
        with self.assertRaises(ValueError):
            tick(self.path, "backwards", self.now - timedelta(seconds=1), {"kind": "wait"})
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT count FROM encounters WHERE person_id = 'ivo'").fetchone()[0], 1)

    def test_lateness_is_not_repeated_and_inspector_is_event_bound(self):
        self.act({"kind": "move", "place_id": "repair-shop"})
        self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.act({"kind": "move", "place_id": "records-office"}, 2)
        late = self.act({"kind": "wait"}, 5)
        self.assertIn("deadline_missed", [event["kind"] for event in late["events"]])
        quiet = self.act({"kind": "wait"}, 5)
        self.assertFalse(quiet["publish"])
        handoff = self.act({"kind": "deliver", "delivery_id": "parcel-1"}, 5)
        self.assertIn("inspection", [event["kind"] for event in handoff["events"]])
        self.act({"kind": "collect", "delivery_id": "parcel-2"}, 5)
        self.act({"kind": "move", "place_id": "repair-shop"}, 5)
        self.act({"kind": "deliver", "delivery_id": "parcel-2"}, 5)
        queued = next(item for item in snapshot(self.path)["deliveries"] if item["status"] == "queued")
        self.act({"kind": "collect", "delivery_id": queued["id"]}, 5)
        revisit = self.act({"kind": "move", "place_id": "records-office"}, 5)
        self.assertNotIn("inspector-ren", [event.get("person_id") for event in revisit["events"]])
        self.assertIn("encounter", [event["kind"] for event in revisit["events"]])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM arrivals").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count FROM encounters WHERE person_id = 'inspector-ren'").fetchone()[0], 1)
        state = snapshot(self.path)
        self.assertEqual(len(state["deliveries"]), 3)  # Fresh work after the queue is finished.
        self.assertEqual(sum(item["status"] == "carried" for item in state["deliveries"]), 1)

    def test_nighttime_is_not_forced_sleep(self):
        result = self.act({"kind": "move", "place_id": "repair-shop"}, 11)
        self.assertEqual(result["action"]["kind"], "move")
        self.assertIn("first_meeting", [event["kind"] for event in result["events"]])
