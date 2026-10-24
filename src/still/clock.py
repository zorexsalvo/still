"""World time is real UTC time; sleep classification applies only to downtime."""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from still.db import session
from still.models import Downtime, WorldClock


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("world timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def split_downtime(start: datetime, end: datetime):
    start = datetime.fromisoformat(timestamp(start))
    end = datetime.fromisoformat(timestamp(end))
    if end < start:
        raise ValueError("downtime cannot end before it starts")
    while start < end:
        if start.hour < 6:
            kind = "sleep"
            boundary = datetime.combine(start.date(), time(6), UTC)
        elif start.hour < 22:
            kind = "mundane"
            boundary = datetime.combine(start.date(), time(22), UTC)
        else:
            kind = "sleep"
            boundary = datetime.combine(start.date() + timedelta(days=1), time(6), UTC)
        stop = min(boundary, end)
        yield start, stop, kind
        start = stop


def resume(path: Path, now: datetime) -> None:
    current = timestamp(now)
    with session(path, immediate=True) as db:
        WorldClock.__table__.create(db.connection(), checkfirst=True)
        Downtime.__table__.create(db.connection(), checkfirst=True)
        clock = db.get(WorldClock, 1)
        if clock is None:
            db.add(WorldClock(id=1, last_seen=current))
        else:
            for start, end, kind in split_downtime(datetime.fromisoformat(clock.last_seen), now):
                db.add(Downtime(started_at=timestamp(start), ended_at=timestamp(end), kind=kind))
            clock.last_seen = current


def checkpoint(path: Path, now: datetime) -> None:
    current = timestamp(now)
    with session(path, immediate=True) as db:
        clock = db.get(WorldClock, 1)
        if clock is not None and current > clock.last_seen:
            clock.last_seen = current
