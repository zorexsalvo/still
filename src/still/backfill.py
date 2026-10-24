"""Explicitly requested, labelled history; never rewind the live courier."""

import argparse
import logging
import os
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from still.clock import timestamp
from still.db import create_tables, session
from still.engine import run_tick
from still.models import BackfillTick, Courier, Entry, Event, Publication, Tick
from still.world import initialize


log = logging.getLogger(__name__)


def backfill(path: Path, day: date, base_url: str, model: str) -> int:
    if day >= datetime.now(UTC).date():
        raise ValueError("backfill must be a completed past UTC day")
    if not path.is_file():
        raise ValueError("start the live world before adding history")
    label = day.isoformat()
    with session(path, immediate=True) as db:
        create_tables(db)
        if db.get(Courier, 1) is None:
            raise ValueError("live world has not been initialized")
        if db.scalar(select(BackfillTick.tick_id).where(BackfillTick.day == label).limit(1)) is not None:
            raise ValueError(f"{label} has already been backfilled")
    start = datetime.combine(day, time(), UTC)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "history.sqlite3"
        initialize(source, start)
        for hour in range(24):
            result = run_tick(source, f"hour-{hour}", base_url, model,
                              now=start + timedelta(hours=hour),
                              hardware={"scope": "backfill", "temperature_c": None})
            if result["error"]:
                raise RuntimeError(f"Backfill stopped at {hour:02}:00 UTC: {result['error']}; no history imported")
            log.info("%s %02d:00 UTC: %s", label, hour, "entry" if result["entry"] else "quiet")
        with session(source, readonly=True) as history, session(path, immediate=True) as live:
            if live.scalar(select(BackfillTick.tick_id).where(BackfillTick.day == label).limit(1)) is not None:
                raise ValueError(f"{label} has already been backfilled")
            entries = {entry.tick_id: entry for entry in history.scalars(select(Entry))}
            if not entries:
                raise ValueError("No entries were generated; live history was left unchanged")
            generated_at = timestamp(datetime.now(UTC))
            for old in history.scalars(select(Tick).order_by(Tick.id)):
                current = Tick(tick_key=f"backfill:{label}:{old.tick_key}", at=old.at,
                               action=old.action, hardware=old.hardware, publish=old.publish)
                live.add(current)
                live.flush()
                live.add(BackfillTick(tick_id=current.id, day=label, generated_at=generated_at, model=model))
                publication = history.get(Publication, old.id)
                live.add(Publication(tick_id=current.id, status=publication.status, error=publication.error))
                for event in history.scalars(select(Event).where(Event.tick_id == old.id).order_by(Event.id)):
                    key = f"backfill:{label}:{event.event_key}" if event.event_key else None
                    live.add(Event(tick_id=current.id, kind=event.kind, facts=event.facts, event_key=key))
                if old.id in entries:
                    live.add(Entry(tick_id=current.id, at=entries[old.id].at, text=entries[old.id].text))
            return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, default=datetime.now(UTC).date() - timedelta(days=1))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        count = backfill(
            Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3")), args.day,
            os.environ.get("STILL_MODEL_BASE_URL", "http://127.0.0.1:8080/v1"),
            os.environ.get("STILL_MODEL", "still"),
        )
    except (ValueError, RuntimeError) as failure:
        parser.exit(1, f"{failure}\n")
    print(f"Added {count} backdated entries for {args.day} UTC. Live courier state was not changed.")


if __name__ == "__main__":
    main()
