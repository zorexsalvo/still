import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from still.db import session
from still.engine import run_tick
from still.models import Delivery, Event, Person, Place, Tick
from still.simulation import options, tick
from still.world import WORK, WORLD_GROWTH, initialize, snapshot
from still.world_view import world_page


class JobsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'jobs.sqlite3'
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        initialize(self.path, self.now)
        self.number = 0
        self.clear_work()
        self.at = self.now + timedelta(hours=6)

    def clear_work(self):
        with session(self.path) as db:
            for delivery in db.scalars(select(Delivery)):
                delivery.status = 'delivered'

    def act(self, action, *, at=None, hardware=None):
        self.number += 1
        return tick(self.path, f'job-test-{self.number}', at or self.at, action, hardware, grow=False)

    def open_batch(self):
        self.act({'kind': 'wait'})
        return [row for row in snapshot(self.path)['deliveries'] if row['status'] == 'queued']

    def carry(self, job):
        if snapshot(self.path)['courier']['place_id'] != job['origin_id']:
            self.act({'kind': 'move', 'place_id': job['origin_id']})
        self.act({'kind': 'collect', 'delivery_id': job['id']})
        self.act({'kind': 'move', 'place_id': job['destination_id']})

    def test_jobs_compete_at_different_stops_and_expose_their_purpose(self):
        jobs = self.open_batch()
        self.assertEqual(len(jobs), 2)
        self.assertEqual({job['due_at'] for job in jobs}, {
            (self.at + timedelta(hours=2)).isoformat(timespec='microseconds'),
            (self.at + timedelta(hours=4)).isoformat(timespec='microseconds'),
        })
        work = [action for action in options(self.path, now=self.at) if action['kind'] == 'move']
        self.assertEqual(len(work), 2)
        for job in jobs:
            self.assertTrue(job['job']['purpose'])
            self.assertTrue(job['job']['output'])
            self.assertIn(job['job']['purpose'], world_page(self.path, now=self.at).body.decode())

    def test_delivery_enables_one_followup_and_replay_does_not_duplicate_it(self):
        job = self.open_batch()[0]
        self.carry(job)
        result = self.act({'kind': 'deliver', 'delivery_id': job['id']})
        enabled = next(event for event in result['events'] if event['kind'] == 'work_enabled')
        self.assertTrue(result['publish'])
        followup = next(row for row in snapshot(self.path)['deliveries'] if row['id'] == enabled['followup_id'])
        self.assertEqual(followup['contents'], WORK[job['recipient_id']][2])
        self.assertEqual(followup['sender_id'], job['recipient_id'])
        self.assertEqual(followup['job']['prerequisite'], job['id'])
        before = snapshot(self.path)
        again = tick(self.path, f'job-test-{self.number}', self.at, {'kind': 'wait'}, grow=False)
        self.assertEqual(result, again)
        initialize(self.path, self.at + timedelta(hours=1))
        self.assertEqual(snapshot(self.path), before)
        self.carry(followup)
        finished = self.act({'kind': 'deliver', 'delivery_id': followup['id']})
        self.assertNotIn('work_enabled', [event['kind'] for event in finished['events']])
        self.assertEqual(len(snapshot(self.path)['deliveries']), 5)

    def test_lateness_holds_up_work_until_the_input_arrives(self):
        job = self.open_batch()[0]
        self.carry(job)
        late_at = datetime.fromisoformat(job['due_at']) + timedelta(minutes=10)
        late = self.act({'kind': 'wait'}, at=late_at)
        missed = next(event for event in late['events'] if event['kind'] == 'deadline_missed')
        self.assertIn('work was held up', missed['text'])
        self.assertEqual(len(snapshot(self.path)['deliveries']), 4)
        quiet = self.act({'kind': 'wait'}, at=late_at)
        self.assertNotIn('deadline_missed', [event['kind'] for event in quiet['events']])
        recovered = self.act({'kind': 'deliver', 'delivery_id': job['id']}, at=late_at)
        self.assertIn('work_enabled', [event['kind'] for event in recovered['events']])
        self.assertEqual(len(snapshot(self.path)['deliveries']), 5)

    def test_refusal_cancels_the_output_instead_of_unlocking_it(self):
        job = self.open_batch()[0]
        self.carry(job)
        result = self.act({'kind': 'deliver', 'delivery_id': job['id']},
                          at=datetime.fromisoformat(job['due_at']) + timedelta(hours=13))
        self.assertIn('work_cancelled', [event['kind'] for event in result['events']])
        self.assertNotIn('work_enabled', [event['kind'] for event in result['events']])
        self.assertEqual(len(snapshot(self.path)['deliveries']), 4)
        self.assertIn('would not be ready', world_page(self.path, now=self.at).body.decode())

    def test_recurring_batches_revisit_every_regular_not_just_ivo_and_mara(self):
        with session(self.path) as db:
            for place_id, name, kind, person_id, person_name, role, _ in WORLD_GROWTH:
                db.add(Place(id=place_id, name=name, kind=kind))
                db.flush()
                db.add(Person(id=person_id, name=person_name, role=role, place_id=place_id))
        recipients = set()
        for _ in range(len(WORK)):
            recipients.update(job['recipient_id'] for job in self.open_batch())
            self.clear_work()
            self.at += timedelta(hours=6)
        self.assertEqual(recipients, set(WORK))

    def test_long_gap_opens_only_one_batch_and_pending_work_bounds_the_queue(self):
        self.at += timedelta(days=30)
        self.assertEqual(len(self.open_batch()), 2)
        self.act({'kind': 'wait'}, at=self.at + timedelta(days=30))
        self.assertEqual(len(snapshot(self.path)['deliveries']), 4)

    def test_pressure_keeps_handover_legal_but_blocks_travel(self):
        job = self.open_batch()[0]
        pressure = {'load_1m': 20, 'cpu_count': 1}
        self.assertEqual(options(self.path, pressure, now=self.at), [{'kind': 'wait'}])
        self.carry(job)
        self.assertIn({'kind': 'deliver', 'delivery_id': job['id']}, options(self.path, pressure, now=self.at))
        result = self.act({'kind': 'deliver', 'delivery_id': job['id']}, hardware=pressure)
        self.assertIn('work_enabled', [event['kind'] for event in result['events']])

    def test_failed_followup_rolls_back_handover_and_events_together(self):
        job = self.open_batch()[0]
        self.carry(job)
        before = snapshot(self.path)
        with patch('still.simulation._job', side_effect=RuntimeError('failed to create followup')):
            with self.assertRaises(RuntimeError):
                self.act({'kind': 'deliver', 'delivery_id': job['id']})
        self.assertEqual(snapshot(self.path), before)
        with session(self.path, readonly=True) as db:
            self.assertIsNone(db.scalar(select(Tick).where(Tick.tick_key == f'job-test-{self.number}')))
            self.assertIsNone(db.scalar(select(Event).where(Event.kind == 'work_enabled')))

    @patch('still.engine.generate', side_effect=ValueError('do not publish a demonstration'))
    @patch('still.engine.choose')
    def test_work_consequence_reaches_writer_without_an_extra_model_call(self, choose, generate):
        job = self.open_batch()[0]
        self.carry(job)
        choose.return_value = {'kind': 'deliver', 'delivery_id': job['id']}
        result = run_tick(self.path, 'model-delivery', 'http://local/v1', 'still', now=self.at)
        choose.assert_called_once()
        choice_state = choose.call_args.args[3]
        self.assertEqual(len(choice_state['deliveries']), 2)
        self.assertTrue(all(parcel['status'] in ('queued', 'carried') for parcel in choice_state['deliveries']))
        generate.assert_called_once()
        self.assertIn(job['job']['activity'], generate.call_args.args[2])
        self.assertIn(job['job']['output'], generate.call_args.args[2])
        self.assertIsNone(result['entry'])
