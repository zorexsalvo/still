"""Opt-in real-model/HTTP check, entirely inside a temporary world."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from html import escape
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy import select

from still.db import session
from still.engine import run_tick
from still.models import Delivery, Entry, Tick
from still.worker import claim, next_tick, prepare, release, run_once


def main():
    model = os.environ.get("STILL_MODEL", "still")
    base_url = os.environ.get("STILL_MODEL_BASE_URL", "http://127.0.0.1:8080/v1")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "smoke.sqlite3"
        owner = "isolated-live-smoke"
        claim(path, owner)
        try:
            prepare(path)
            # A finished-round fixture leaves real model choices among personal actions, not routine receipts.
            with session(path) as db:
                for delivery in db.scalars(select(Delivery)):
                    delivery.status = "delivered"
            assert asyncio.run(run_once(path, base_url, model, owner))
            due = next_tick(path)
            with session(path, readonly=True) as db:
                entry = db.scalar(select(Entry))
                # shortcut: this demo expects an eligible personal moment; inspect legitimate silence or rejection before rerunning.
                assert entry is not None, "Personal tick was silent or rejected; inspect the model/host conditions"
                first = db.get(Tick, entry.tick_id)
                text, key = entry.text, first.tick_key
                action = first.action
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                web_port = port.getsockname()[1]
            web = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "still:app", "--host", "127.0.0.1", "--port", str(web_port)],
                env={**os.environ, "STILL_DB_PATH": str(path)},
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            try:
                url = f"http://127.0.0.1:{web_port}"
                for _ in range(30):
                    try:
                        with urlopen(url, timeout=2) as response:
                            body = response.read().decode()
                        break
                    except URLError:
                        if web.poll() is not None:
                            raise RuntimeError(web.stderr.read().decode())
                        time.sleep(.2)
                else:
                    raise RuntimeError("temporary viewer did not start")
                assert escape(text) in body and "All times UTC" in body
                with urlopen(url + "/static/diary.css", timeout=2) as response:
                    assert response.status == 200
                # A bound, non-listening port reliably simulates unavailable inference.
                with socket.socket() as offline:
                    offline.bind(("127.0.0.1", 0))
                    unavailable = f"http://127.0.0.1:{offline.getsockname()[1]}/v1"
                    replay = run_tick(path, key, unavailable, model, owner=owner)
                    assert replay["entry"]["text"] == text
                    failed = run_tick(path, "offline-check", unavailable, model, owner=owner)
                    assert failed["entry"] is None and failed["error"]
                    with urlopen(url, timeout=2) as response:
                        assert response.status == 200 and escape(text) in response.read().decode()
                prepare(path)
                assert next_tick(path) == due
                assert not asyncio.run(run_once(path, base_url, model, owner))
                print(json.dumps({"action": action, "entry": text, "next_tick": due.isoformat(),
                                  "checks": "real model -> SQLite -> HTTP; offline reading; replay; restart rest preserved"}, indent=2))
            finally:
                web.terminate()
                try:
                    web.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    web.kill()
                    web.wait()
                web.stderr.close()
        finally:
            release(path, owner)


if __name__ == "__main__":
    main()
