import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from still import clock
from still.clock import checkpoint, resume, split_downtime, timestamp


def utc(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


class ClockTest(unittest.TestCase):
    def test_boundaries_and_multi_day_gap(self):
        self.assertEqual(list(split_downtime(utc(24, 21), utc(26, 7))), [
            (utc(24, 21), utc(24, 22), "mundane"),
            (utc(24, 22), utc(25, 6), "sleep"),
            (utc(25, 6), utc(25, 22), "mundane"),
            (utc(25, 22), utc(26, 6), "sleep"),
            (utc(26, 6), utc(26, 7), "mundane"),
        ])

    def test_exact_boundaries_and_empty_gap(self):
        for start, end, kind in [
            (utc(24, 0), utc(24, 6), "sleep"),
            (utc(24, 6), utc(24, 22), "mundane"),
            (utc(24, 22), utc(25, 6), "sleep"),
        ]:
            self.assertEqual(list(split_downtime(start, end)), [(start, end, kind)])
        self.assertEqual(list(split_downtime(utc(24, 6), utc(24, 6))), [])

    def test_timezone_normalization_and_invalid_times(self):
        local = datetime(2026, 10, 25, 6, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(timestamp(local), timestamp(utc(24, 22)))
        with self.assertRaises(ValueError):
            timestamp(datetime(2026, 10, 24))
        with self.assertRaises(ValueError):
            list(split_downtime(utc(25, 6), utc(24, 22)))

    def test_resume_records_only_gap_since_last_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "still.sqlite3"
            resume(path, utc(24, 20))
            checkpoint(path, utc(24, 23))  # Live nighttime is not logged as sleep.
            resume(path, utc(25, 7))
            resume(path, utc(25, 7))  # No duplicate gap at the same instant.
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT started_at, ended_at, kind FROM downtime ORDER BY id"
                ).fetchall(), [
                    (timestamp(utc(24, 23)), timestamp(utc(25, 6)), "sleep"),
                    (timestamp(utc(25, 6)), timestamp(utc(25, 7)), "mundane"),
                ])
                self.assertEqual(connection.execute(
                    "SELECT last_seen FROM world_clock"
                ).fetchone(), (timestamp(utc(25, 7)),))

    def test_clock_endpoint_is_explicitly_utc(self):
        result = clock()
        self.assertEqual(result["timezone"], "UTC")
        self.assertEqual(datetime.fromisoformat(result["now"]).utcoffset(), timedelta(0))
