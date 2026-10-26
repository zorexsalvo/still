import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from still.db import session
from still.models import Delivery, Encounter
from still.simulation import WRITING_EVENTS, options, tick
from still.world import CONTENTS, GROWTH_INTERVAL, WORLD_GROWTH, initialize, snapshot


def finish_seeded_round(path):
    """Clear the seeded parcels so the next tick opens a new round of work."""
    with session(path) as db:
        for parcel in db.scalars(select(Delivery)):
            parcel.status = "delivered"


class SimulationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "world.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)
        self.number = 0

    def act(self, action, hours=0, *, grow=True):
        self.number += 1
        return tick(self.path, str(self.number), self.now + timedelta(hours=hours), action, grow=grow)

    def test_a_new_place_and_person_arrive_with_the_round_that_reaches_them(self):
        finish_seeded_round(self.path)
        # The next observed batch reaches the first authored stop.
        opened = self.act({"kind": "wait"}, 24)
        self.assertIn("new_place", [event["kind"] for event in opened["events"]])
        state = snapshot(self.path)
        self.assertIn(("bindery", "Bindery"), [(place["id"], place["name"]) for place in state["places"]])
        self.assertIn(("nadia", "Nadia", "bindery"),
                      [(p["id"], p["name"], p["place_id"]) for p in state["people"]])
        # The round starts there, so the new place becomes somewhere the courier can travel.
        self.assertIn({"kind": "move", "place_id": "bindery"}, options(self.path))
        self.assertNotIn(("goods-yard", "Goods yard"), [(place["id"], place["name"]) for place in state["places"]])
        parcel = next(parcel for parcel in state["deliveries"] if parcel["sender_id"] == "nadia")
        self.assertEqual((parcel["sender_id"], parcel["recipient_id"], parcel["origin_id"], parcel["destination_id"]),
                         ("nadia", "mara", "bindery", "records-office"))

    def test_the_route_grows_one_place_at_a_time_and_stops_when_the_list_runs_out(self):
        finish_seeded_round(self.path)
        introduced = []
        for day in range(1, (len(WORLD_GROWTH) + 1) * GROWTH_INTERVAL + 2):
            self.act({"kind": "wait"}, 24 * day)
            introduced = [(place["id"], place["name"]) for place in snapshot(self.path)["places"]]
            if len(introduced) > 3 + len(WORLD_GROWTH):
                self.fail("the route invented a place that is not in the roster")
            finish_seeded_round(self.path)
        self.assertEqual(3 + len(WORLD_GROWTH), len(introduced))
        self.assertIn(("bindery", "Bindery"), introduced)
        self.assertIn(("goods-yard", "Goods yard"), introduced)
        self.assertIn(("print-room", "Print room"), introduced)
        self.assertIn(("ferry-depot", "Ferry depot"), introduced)
        self.assertIn(("map-room", "Map room"), introduced)

    def test_legal_route_and_quiet_ticks(self):
        self.assertNotIn({"kind": "collect", "delivery_id": "parcel-1"}, options(self.path))
        moved = self.act({"kind": "move", "place_id": "repair-shop"})
        self.assertFalse(moved["publish"])
        collected = self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.assertFalse(collected["publish"])
        self.assertNotIn({"kind": "collect", "delivery_id": "parcel-2"}, options(self.path))
        self.act({"kind": "move", "place_id": "records-office"}, 2)
        delivered = self.act({"kind": "deliver", "delivery_id": "parcel-1"}, 3)
        self.assertFalse(delivered["publish"])
        self.assertIn("I delivered the corrected directory index to Mara.", delivered["events"][0]["text"])
        self.assertEqual(snapshot(self.path)["deliveries"][0]["status"], "delivered")

    def test_candidates_are_interruptions_or_personal_threads_not_routine_work(self):
        self.assertTrue({"deadline_missed", "delivery_failed", "delivery_refused", "inspection",
                         "file_recovered", "file_completed", "personal_visit"} <= WRITING_EVENTS)
        self.assertTrue(WRITING_EVENTS.isdisjoint({"delivered", "collected", "moved", "first_meeting", "waited"}))

    def test_courier_cannot_leave_a_pickup_to_bounce_to_another_stop(self):
        self.act({"kind": "move", "place_id": "repair-shop"})
        self.assertEqual(options(self.path), [
            {"kind": "collect", "delivery_id": "parcel-1"}, {"kind": "restore_file"},
            {"kind": "visit", "person_id": "ivo"}, {"kind": "wait"},
        ])
        before = snapshot(self.path)
        with self.assertRaisesRegex(ValueError, "illegal"):
            self.act({"kind": "move", "place_id": "records-office"}, 1)
        self.assertEqual(snapshot(self.path), before)
        self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.assertEqual(options(self.path), [
            {"kind": "move", "place_id": "records-office"}, {"kind": "restore_file"},
            {"kind": "visit", "person_id": "ivo"}, {"kind": "wait"},
        ])

    def test_move_biased_choices_still_complete_deliveries_over_a_day(self):
        completed = []
        for hour in range(24):
            now = self.now + timedelta(hours=hour)
            actions = options(self.path, now=now)
            # Reproduce the model's old travel preference, without letting it wait forever.
            action = next((action for action in actions if action["kind"] == "move"), actions[0])
            result = self.act(action, hour)
            if any(event["kind"] == "delivered" for event in result["events"]):
                self.assertEqual(result["publish"], any(
                    event["kind"] in ("inspection", "deadline_missed", "work_enabled") for event in result["events"]
                ))
            completed.extend(event["delivery_id"] for event in result["events"] if event["kind"] == "delivered")
        self.assertGreater(len(completed), 3)  # Competing batches return without waiting for UTC midnight.
        self.assertIn("parcel-1", completed)
        self.assertIn("parcel-2", completed)

    def test_competing_jobs_carry_inputs_their_recipients_need(self):
        # Loads now follow the recipient's work instead of an unrelated parcel-number cycle.
        finish_seeded_round(self.path)
        for day in range(1, 8):
            self.act({"kind": "wait"}, 24 * day, grow=False)
            finish_seeded_round(self.path)
        with session(self.path) as db:
            created = [row.contents for row in db.scalars(select(Delivery).where(
                Delivery.id.not_in(("parcel-1", "parcel-2"))
            ).order_by(Delivery.created_at))]
        self.assertGreaterEqual(len(created), 5)
        self.assertTrue(all(created), "every round records what it carries")
        self.assertEqual(set(created), {"access request", "corrected directory index"})
        self.assertTrue(all(created[i] != created[i + 1] for i in range(0, len(created), 2)))

    def test_writer_facts_describe_contents_not_internal_parcel_ids(self):
        facts = []
        for hour in range(24):
            result = self.act(options(self.path, now=self.now + timedelta(hours=hour))[0], hour)
            facts.extend(event["text"] for event in result["events"])
        prose = " ".join(facts)
        self.assertNotIn("parcel-", prose.lower())
        found = {description for description in CONTENTS if description in prose}
        self.assertGreaterEqual(len(found), 2)

    def test_named_database_ids_remain_collectible(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE deliveries SET id = 'corrected directory index' WHERE id = 'parcel-1'")
        self.act({"kind": "move", "place_id": "repair-shop"})
        result = self.act({"kind": "collect", "delivery_id": "corrected directory index"}, 1)
        self.assertEqual(result["events"][0]["text"], "I collected the corrected directory index.")

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
        # Pinned to the two established stops so this stays about lateness, not about new routes.
        self.act({"kind": "wait"}, 24, grow=False)
        queued = next(item for item in snapshot(self.path)["deliveries"] if item["status"] == "queued")
        self.act({"kind": "collect", "delivery_id": queued["id"]}, 24)
        revisit = self.act({"kind": "move", "place_id": "records-office"}, 24)
        self.assertNotIn("inspector-ren", [event.get("person_id") for event in revisit["events"]])
        self.assertIn("encounter", [event["kind"] for event in revisit["events"]])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM arrivals").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count FROM encounters WHERE person_id = 'inspector-ren'").fetchone()[0], 1)
        state = snapshot(self.path)
        self.assertEqual(len(state["deliveries"]), 4)  # A competing pair after the seed queue is finished.
        self.assertEqual(sum(item["status"] == "carried" for item in state["deliveries"]), 1)

    def test_nighttime_is_not_forced_sleep(self):
        result = self.act({"kind": "move", "place_id": "repair-shop"}, 11)
        self.assertEqual(result["action"]["kind"], "move")
        self.assertIn("first_meeting", [event["kind"] for event in result["events"]])

    def test_a_handover_offered_far_too_late_is_refused_not_accepted(self):
        self.act({"kind": "move", "place_id": "repair-shop"})
        self.act({"kind": "collect", "delivery_id": "parcel-1"}, 1)
        self.act({"kind": "move", "place_id": "records-office"}, 2)
        # parcel-1 is due four hours after the world starts; well past the refusal window.
        refused = self.act({"kind": "deliver", "delivery_id": "parcel-1"}, 24)
        kinds = [event["kind"] for event in refused["events"]]
        self.assertIn("delivery_refused", kinds)
        self.assertNotIn("delivered", kinds)
        self.assertNotIn("inspection", kinds)
        self.assertTrue(refused["publish"])
        self.assertEqual(snapshot(self.path)["deliveries"][0]["status"], "returned")
