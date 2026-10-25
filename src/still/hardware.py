"""Optional native host readings; never mistake a Docker VM for the Mac."""

import argparse
import json
import math
import os
import platform
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from still.clock import timestamp


def temperature() -> float | None:
    # Pi thermal-zone files are readable without elevated privileges.
    for root in (Path("/host/sys/class/thermal"), Path("/sys/class/thermal")):
        for sensor in sorted(root.glob("thermal_zone*/temp")):
            try:
                value = int(sensor.read_text().strip())
                return value / 1000 if value > 1000 else float(value)
            except (OSError, ValueError):
                continue
    return None


def endpoint_latency_ms(base_url: str | None = None) -> float | None:
    base_url = base_url or os.environ.get("STILL_MODEL_BASE_URL")
    if not base_url:
        return None
    request = urllib.request.Request(base_url.rstrip("/") + "/models", method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=2):
            return round((time.monotonic() - started) * 1000, 1)
    except (OSError, urllib.error.URLError, ValueError):
        return None


def sample(path: Path) -> dict:
    return {
        "sampled_at": timestamp(datetime.now(UTC)),
        "platform": platform.system(),
        "scope": "worker",
        "load_1m": os.getloadavg()[0],
        "cpu_count": os.cpu_count() or 1,
        "disk_free_bytes": shutil.disk_usage(path).free,
        "temperature_c": temperature(),
        "model_endpoint_ms": endpoint_latency_ms(),
    }


def readings(path: Path) -> dict:
    try:
        source = path.parent / "host-hardware.json"
        if source.stat().st_size > 4096:
            raise ValueError("host reading is too large")
        value = json.loads(source.read_text())
        sampled_at = datetime.fromisoformat(value["sampled_at"])
        if sampled_at.tzinfo is None:
            raise ValueError("host reading must include timezone")
        age = (datetime.now(UTC) - sampled_at).total_seconds()
        load = value["load_1m"]
        cores = value["cpu_count"]
        if (not 0 <= age <= 90 or type(load) not in (int, float) or not math.isfinite(load)
                or load < 0 or type(cores) is not int or cores < 1
                or type(value.get("disk_free_bytes")) is not int or value["disk_free_bytes"] < 0
                or not isinstance(value.get("platform"), str) or not 1 <= len(value["platform"]) <= 64
                or (value.get("temperature_c") is not None and
                    (type(value["temperature_c"]) not in (int, float) or not math.isfinite(value["temperature_c"])
                     or not 0 <= value["temperature_c"] <= 150))
                or (value.get("model_endpoint_ms") is not None and
                    (type(value["model_endpoint_ms"]) not in (int, float)
                     or not math.isfinite(value["model_endpoint_ms"]) or value["model_endpoint_ms"] < 0))
                or value.get("scope") != "native-host"):
            raise ValueError("invalid or stale host reading")
        value.setdefault("model_endpoint_ms", None)
        return {key: value[key] for key in (
            "sampled_at", "platform", "scope", "load_1m", "cpu_count", "disk_free_bytes", "temperature_c",
            "model_endpoint_ms"
        )}
    except (OSError, ValueError, KeyError, TypeError):
        value = sample(path.parent)
        value["host_reading"] = "unavailable or stale; worker readings may describe a Docker VM"
        return value


def under_pressure(value: dict) -> bool:
    return (value.get("load_1m", 0) / value.get("cpu_count", 1) > 1.5
            or (value.get("temperature_c") is not None and value["temperature_c"] >= 80))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("data"))
    parser.add_argument("--watch", action="store_true", help="refresh every 30 seconds")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    try:
        while True:
            value = sample(args.directory)
            value["scope"] = "native-host"
            temporary = args.directory / "host-hardware.json.tmp"
            temporary.write_text(json.dumps(value))
            temporary.replace(args.directory / "host-hardware.json")
            if not args.watch:
                break
            time.sleep(30)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
