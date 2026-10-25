import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from still.hardware import endpoint_latency_ms, readings, under_pressure
from still.simulation import options, tick
from still.world import initialize


class HardwareTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "still.sqlite3"
        self.file = self.path.parent / "host-hardware.json"
        self.value = {
            "sampled_at": datetime.now(UTC).isoformat(), "platform": "Darwin", "scope": "native-host",
            "load_1m": 2.0, "cpu_count": 8, "disk_free_bytes": 1000, "temperature_c": None,
            "model_endpoint_ms": None,
        }

    def test_fresh_native_reading_is_used_without_worker_metrics(self):
        self.file.write_text(json.dumps(self.value))
        with patch("still.hardware.sample") as sample:
            self.assertEqual(readings(self.path), self.value)
        sample.assert_not_called()

    @patch("still.hardware.sample", return_value={"scope": "worker", "platform": "Linux"})
    def test_missing_stale_or_invalid_readings_are_explicit_fallbacks(self, sample):
        self.assertIn("unavailable", readings(self.path)["host_reading"])
        for changes in [
            {"sampled_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()},
            {"sampled_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
            {"sampled_at": "2026-10-24T12:00:00"},
            {"load_1m": float("nan")}, {"load_1m": -1}, {"load_1m": True},
            {"cpu_count": 0}, {"cpu_count": "8"}, {"disk_free_bytes": -1},
            {"temperature_c": "hot"}, {"temperature_c": 151}, {"model_endpoint_ms": -1},
            {"platform": None}, {"scope": "invented"},
        ]:
            with self.subTest(changes=changes):
                self.file.write_text(json.dumps({**self.value, **changes}))
                result = readings(self.path)
                self.assertEqual(result["scope"], "worker")
                self.assertIn("Docker VM", result["host_reading"])
        self.file.write_text("broken json")
        self.assertIn("unavailable", readings(self.path)["host_reading"])

    def test_endpoint_latency_probe_never_raises(self):
        # Regression: the probe used urllib without importing it, which raised
        # NameError before its own try and broke the sample() fallback.
        with patch.dict("os.environ", {"STILL_MODEL_BASE_URL": "http://127.0.0.1:9/v1"}):
            self.assertIsNone(endpoint_latency_ms())

    def test_thermal_pressure_has_same_consequence_as_high_load(self):
        initialize(self.path, datetime.now(UTC))
        hot = {**self.value, "temperature_c": 82.5}
        self.assertTrue(under_pressure(hot))
        self.assertEqual(options(self.path, hot), [{"kind": "wait"}])

    def test_load_has_consequences_without_metrics_in_prose_facts(self):
        now = datetime.now(UTC)
        initialize(self.path, now)
        loaded = {**self.value, "load_1m": 20.0}
        self.assertTrue(under_pressure(loaded))
        self.assertFalse(under_pressure(self.value))
        self.assertEqual(options(self.path, loaded), [{"kind": "wait"}])
        with self.assertRaises(ValueError):
            tick(self.path, "illegal", now, {"kind": "move", "place_id": "repair-shop"}, loaded)
        result = tick(self.path, "slow", now, {"kind": "wait"}, loaded)
        self.assertEqual(result["hardware"], loaded)
        self.assertEqual(result["events"][0]["kind"], "slowed")
        self.assertFalse(result["publish"])
        self.assertNotIn("20", result["events"][0]["text"])
