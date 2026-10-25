import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from still.db import session
from still.engine import run_tick, writing_moment
from still.models import Courier, Delivery, PersonalLife, Relationship
from still.simulation import options, tick
from still.world import initialize, snapshot


class ContinuingLifeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'world.sqlite3'
        self.start = datetime(2026, 10, 19, tzinfo=UTC)
        initialize(self.path, self.start)
        self.number = 0

    def at(self, hour):
        return self.start + timedelta(hours=hour)

    def act(self, kind, hour, person=None):
        # Finished-round fixture isolates private choices from delivery scheduling.
        with session(self.path) as db:
            for delivery in db.scalars(select(Delivery)):
                delivery.status = 'delivered'
        self.number += 1
        action = {'kind': kind}
        if person:
            action['person_id'] = person
        return tick(self.path, str(self.number), self.at(hour), action)

    def recover(self):
        for hour in range(3):
            self.act('restore_file', hour)
        self.act('visit', 3, 'ivo')

    def resolve_owner(self):
        self.recover()
        self.act('follow_owner', 4, 'ivo')
        self.act('follow_owner', 22, 'ivo')
        self.act('follow_owner', 23, 'mara')
        return self.act('follow_owner', 41, 'mara')

    def test_replies_take_real_time_and_restart_does_not_supply_them(self):
        self.recover()
        self.act('follow_owner', 4, 'ivo')
        before = snapshot(self.path)
        with self.assertRaisesRegex(ValueError, 'illegal'):
            self.act('follow_owner', 21, 'ivo')
        self.assertEqual(snapshot(self.path), before)
        initialize(self.path, self.at(30))
        self.assertEqual(snapshot(self.path), before)
        self.assertNotIn({'kind': 'follow_owner', 'person_id': 'ivo'}, options(self.path, now=self.at(21)))
        self.assertIn({'kind': 'follow_owner', 'person_id': 'ivo'}, options(self.path, now=self.at(22)))
        self.act('follow_owner', 22, 'ivo')
        self.assertEqual(snapshot(self.path)['personal_life']['owner_stage'], 2)

    @patch('still.engine.options', wraps=options)
    def test_engine_uses_replay_time_not_host_time_for_available_actions(self, available):
        self.recover()
        self.act('follow_owner', 4, 'ivo')
        run_tick(self.path, 'early-reply', 'http://unused/v1', 'still', now=self.at(5))
        available.assert_called_once_with(self.path, None, now=self.at(5))

    def test_owner_inquiry_changes_the_file_and_survives_into_another_day(self):
        result = self.resolve_owner()
        state = snapshot(self.path)
        self.assertTrue(result['publish'])
        self.assertEqual(state['personal_life']['owner_stage'], 4)
        self.assertIn('owner: shared', state['personal_file']['contents'])
        self.assertNotIn('owner: [blank]', state['personal_file']['contents'])
        self.assertIsNone(state['personal_life']['reply_at'])
        self.assertTrue(all('owner field' not in text for text in state['personal_threads']))
        self.assertIn({'kind': 'borrow_notes', 'person_id': 'ivo'}, options(self.path, now=self.at(42)))

    def test_reading_and_returning_leave_memory_and_a_persistent_promise(self):
        self.resolve_owner()
        self.act('borrow_notes', 42, 'ivo')
        before = snapshot(self.path)
        initialize(self.path, self.at(43))
        self.assertEqual(snapshot(self.path), before)
        read = self.act('read_notes', 43)
        self.assertTrue(read['publish'])
        self.assertIn('missing part', snapshot(self.path)['personal_life']['reading_note'])
        moment, references = writing_moment(self.path, read)
        self.assertIn('I owed repair-notes back to Ivo', moment)
        self.assertIn('My reading:', moment)
        self.assertIn('repair-notes', references)
        with self.assertRaisesRegex(ValueError, 'illegal'):
            self.act('read_notes', 43)
        with self.assertRaisesRegex(ValueError, 'illegal'):
            self.act('return_notes', 43, 'mara')
        self.act('return_notes', 44, 'ivo')
        state = snapshot(self.path)
        self.assertIsNone(state['personal_life']['borrowed_from'])
        self.assertEqual(state['relationships'][0]['returns'], 1)
        self.assertNotIn({'kind': 'borrow_notes', 'person_id': 'ivo'}, options(self.path, now=self.at(67)))
        self.assertIn({'kind': 'borrow_notes', 'person_id': 'ivo'}, options(self.path, now=self.at(68)))

    def test_late_promise_is_once_then_refusal_and_help_change_the_relationship(self):
        self.resolve_owner()
        self.act('borrow_notes', 42, 'ivo')
        late = self.act('wait', 67)
        self.assertIn('personal_deadline_missed', [e['kind'] for e in late['events']])
        quiet = self.act('wait', 68)
        self.assertNotIn('personal_deadline_missed', [e['kind'] for e in quiet['events']])
        self.act('return_notes', 69, 'ivo')
        state = snapshot(self.path)
        self.assertEqual(state['relationships'][0]['late_returns'], 1)
        self.assertIn('late return', ' '.join(state['personal_threads']))
        refused = self.act('borrow_notes', 93, 'ivo')
        self.assertIn('personal_refusal', [e['kind'] for e in refused['events']])
        self.assertIsNone(snapshot(self.path)['personal_life']['borrowed_from'])
        with self.assertRaisesRegex(ValueError, 'illegal'):
            self.act('borrow_notes', 94, 'ivo')
        helped = self.act('help_person', 94, 'ivo')
        self.assertIn('personal_help', [e['kind'] for e in helped['events']])
        self.act('borrow_notes', 95, 'ivo')
        self.assertEqual(snapshot(self.path)['relationships'][0]['loans'], 2)
        self.assertEqual(snapshot(self.path)['relationships'][0]['late_returns'], 1)

    def test_delivery_work_can_delay_a_personal_return_and_cause_a_refusal(self):
        self.resolve_owner()
        self.act('borrow_notes', 42, 'ivo')
        with session(self.path) as db:
            parcel = db.get(Delivery, 'parcel-1')
            parcel.created_at, parcel.due_at = self.at(64).isoformat(), self.at(70).isoformat()
            parcel.status = 'carried'
            db.get(Courier, 1).place_id = 'repair-shop'
        working = tick(self.path, 'working', self.at(67), {'kind': 'move', 'place_id': 'records-office'})
        self.assertIn('personal_deadline_missed', [e['kind'] for e in working['events']])
        self.assertNotIn({'kind': 'return_notes', 'person_id': 'ivo'}, options(self.path, now=self.at(68)))
        tick(self.path, 'handoff', self.at(68), {'kind': 'deliver', 'delivery_id': 'parcel-1'})
        tick(self.path, 'return-after-work', self.at(69), {'kind': 'return_notes', 'person_id': 'ivo'})
        self.assertEqual(snapshot(self.path)['relationships'][0]['needs_help'], 1)

    def test_private_choices_continue_across_days_but_rereading_is_quiet(self):
        self.resolve_owner()
        for number in range(5):
            hour = 42 + 26 * number
            self.act('borrow_notes', hour, 'ivo')
            reading = self.act('read_notes', hour + 1)
            self.assertEqual(reading['publish'], number < 4)
            returned = self.act('return_notes', hour + 2, 'ivo')
            self.assertEqual(returned['publish'], number == 0)
        self.assertEqual(snapshot(self.path)['relationships'][0]['read_count'], 5)
        self.assertIn({'kind': 'borrow_notes', 'person_id': 'ivo'}, options(self.path, now=self.at(172)))

    def test_hardware_pressure_still_blocks_private_work(self):
        self.resolve_owner()
        hardware = {'load_1m': 20, 'cpu_count': 1}
        self.assertEqual(options(self.path, hardware, now=self.at(42)), [{'kind': 'wait'}])
        before = snapshot(self.path)
        with self.assertRaisesRegex(ValueError, 'illegal'):
            tick(self.path, 'pressure', self.at(42), {'kind': 'borrow_notes', 'person_id': 'ivo'}, hardware)
        self.assertEqual(snapshot(self.path), before)

    def test_relationship_and_loan_constraints_reject_invalid_state(self):
        with self.assertRaises(IntegrityError), session(self.path) as db:
            db.get(Relationship, 'ivo').returns = 1
        with self.assertRaises(IntegrityError), session(self.path) as db:
            db.get(PersonalLife, 1).borrowed_from = 'ivo'
