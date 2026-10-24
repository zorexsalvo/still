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


def backfill(path: Path, day: date, base_url: str, model: str, *, days: int = 1) -> int:
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    today = datetime.now(UTC).date()
    if day >= today or days > (today - day).days:
        raise ValueError("backfill must contain only completed past UTC days")
    if not path.is_file():
        raise ValueError("start the live world before adding history")
    labels = [(day + timedelta(days=offset)).isoformat() for offset in range(days)]
    with session(path, immediate=True) as db:
        create_tables(db)
        if db.get(Courier, 1) is None:
            raise ValueError("live world has not been initialized")
        existing = db.scalar(select(BackfillTick.day).where(BackfillTick.day.in_(labels)).limit(1))
        if existing is not None:
            raise ValueError(f"{existing} has already been backfilled")
    start = datetime.combine(day, time(), UTC)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "history.sqlite3"
        initialize(source, start)
        for hour in range(24 * days):
            at = start + timedelta(hours=hour)
            result = run_tick(source, f"hour-{hour}", base_url, model,
                              now=at,
                              hardware={"scope": "backfill", "temperature_c": None})
            if result["error"]:
                raise RuntimeError(f"Backfill stopped at {at.isoformat()}: {result['error']}; no history imported")
            log.info("%s: %s", at.isoformat(), "entry" if result["entry"] else "quiet")
        with session(source, readonly=True) as history, session(path, immediate=True) as live:
            existing = live.scalar(select(BackfillTick.day).where(BackfillTick.day.in_(labels)).limit(1))
            if existing is not None:
                raise ValueError(f"{existing} has already been backfilled")
            entries = {entry.tick_id: entry for entry in history.scalars(select(Entry))}
            if not entries:
                raise ValueError("No entries were generated; live history was left unchanged")
            generated_at = timestamp(datetime.now(UTC))
            for old in history.scalars(select(Tick).order_by(Tick.id)):
                label = datetime.fromisoformat(old.at).date().isoformat()
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
    parser.add_argument("--day", type=date.fromisoformat, help="first UTC day; defaults to the last completed days")
    parser.add_argument("--days", type=int, default=1, help="consecutive days to simulate without resetting (default: 1)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.days < 1:
            raise ValueError("days must be a positive integer")
        day = args.day or (datetime.now(UTC).date() - timedelta(days=args.days))
        count = backfill(
            Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3")), day,
            os.environ.get("STILL_MODEL_BASE_URL", "http://127.0.0.1:8080/v1"),
            os.environ.get("STILL_MODEL", "still"), days=args.days,
        )
    except (ValueError, RuntimeError, OverflowError) as failure:
        parser.exit(1, f"{failure}\n")
    print(f"Added {count} backdated entries across {args.days} day(s) from {day} UTC. Live courier state was not changed.")


if __name__ == "__main__":
    main()
