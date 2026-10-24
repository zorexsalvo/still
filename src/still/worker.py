"""Single writer: complete a tick, then rest one real hour."""

import argparse
import asyncio
import fcntl
import logging
import os
import signal
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from still.clock import resume, timestamp
from still.db import require_owner, session
from still.models import Publication, Tick, WorkerLease, WorkerRun, WorkerState, WorldClock
from still.engine import run_tick
from still.hardware import readings
from still.world import initialize


REST = timedelta(hours=1)
log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def claim(path: Path, owner: str) -> None:
    now = utcnow()
    with session(path, immediate=True) as db:
        WorkerLease.__table__.create(db.connection(), checkfirst=True)
        lease = db.get(WorkerLease, 1)
        if lease is not None:
            age = (now - datetime.fromisoformat(lease.heartbeat_at)).total_seconds()
            if age <= 90:
                raise RuntimeError("Another Still worker holds a live database lease")
            lease.owner = owner
            lease.heartbeat_at = timestamp(now)
        else:
            db.add(WorkerLease(id=1, owner=owner, heartbeat_at=timestamp(now)))


def release(path: Path, owner: str) -> None:
    with session(path, immediate=True) as db:
        lease = db.get(WorkerLease, 1)
        if lease is not None and lease.owner == owner:
            db.delete(lease)


def renew(path: Path, owner: str) -> None:
    now = timestamp(utcnow())
    with session(path, immediate=True) as db:
        lease = db.get(WorkerLease, 1)
        if lease is None or lease.owner != owner:
            raise RuntimeError("Still worker lost its database lease")
        lease.heartbeat_at = now
        clock = db.get(WorldClock, 1)
        if now > clock.last_seen:
            clock.last_seen = now


def prepare(path: Path) -> None:
    now = utcnow()
    initialize(path, now)
    resume(path, now)
    with session(path, immediate=True) as db:
        for publication in db.scalars(select(Publication).where(Publication.status == "writing")):
            publication.status = "failed"
            publication.error = "generation interrupted; not retried"
        for tick_id in db.scalars(select(Tick.id).where(Tick.id.not_in(select(Publication.tick_id)))):
            db.add(Publication(tick_id=tick_id, status="failed", error="tick interrupted before publication; not retried"))
        state = db.get(WorkerState, 1)
        if state and state.active_tick_key:
            run = db.get(WorkerRun, state.active_tick_key)
            run.finished_at = timestamp(now)
            run.error = "worker interrupted; not retried"
            state.active_tick_key = None
            state.next_tick_at = timestamp(now + REST)
        elif state is None:
            last = db.scalar(select(Tick).order_by(Tick.id.desc()).limit(1))
            due = datetime.fromisoformat(last.at) + REST if last else now
            db.add(WorkerState(id=1, next_tick_at=timestamp(due)))


def next_tick(path: Path) -> datetime:
    with session(path, readonly=True) as db:
        return datetime.fromisoformat(db.get(WorkerState, 1).next_tick_at)


async def run_once(path: Path, base_url: str, model: str, owner: str | None = None) -> bool:
    started = utcnow()
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        state = db.get(WorkerState, 1)
        if state.active_tick_key:
            raise RuntimeError("unfinished tick must be recovered before running")
        if started < datetime.fromisoformat(state.next_tick_at):
            return False
        key = str(uuid.uuid4())
        db.add(WorkerRun(tick_key=key, started_at=timestamp(started)))
        db.flush()
        state.active_tick_key = key
    error = None
    unexpected = None
    try:
        result = await asyncio.to_thread(run_tick, path, key, base_url, model,
                                         hardware=readings(path), owner=owner)
        error = result["error"]
    except Exception as failure:
        error = str(failure)[:1000]
        unexpected = failure
    finished = utcnow()
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        if owner is not None:
            db.get(WorkerLease, 1).heartbeat_at = timestamp(finished)
        run = db.get(WorkerRun, key)
        run.finished_at = timestamp(finished)
        run.error = error
        state = db.get(WorkerState, 1)
        state.active_tick_key = None
        state.next_tick_at = timestamp(finished + REST)
    log.info("tick %s finished; next eligible tick %s", key, timestamp(finished + REST))
    if error:
        log.warning("tick %s: %s", key, error)
    if unexpected:
        raise unexpected
    return True


async def heartbeat(path: Path, owner: str) -> None:
    while True:
        # shortcut: crashes may add about 30 seconds to downtime; use supervisor stop times if needed.
        await asyncio.sleep(30)
        await asyncio.to_thread(renew, path, owner)


async def serve(path: Path, base_url: str, model: str, owner: str) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    prepare(path)
    task = asyncio.create_task(heartbeat(path, owner))
    try:
        while not stop.is_set():
            if task.done():
                task.result()
            await run_once(path, base_url, model, owner)
            delay = max(0, (next_tick(path) - utcnow()).total_seconds())
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(delay, 30))
            except TimeoutError:
                pass
    finally:
        task.cancel()
        try:
            with suppress(asyncio.CancelledError):
                await task
        finally:
            renew(path, owner)


def healthy(path: Path) -> bool:
    try:
        with session(path, readonly=True) as db:
            clock = db.get(WorldClock, 1)
            if clock is None:
                return False
            last = datetime.fromisoformat(clock.last_seen)
            return 0 <= (utcnow() - last).total_seconds() <= 90
    except (SQLAlchemyError, TypeError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", action="store_true", help="check worker checkpoint freshness")
    args = parser.parse_args()
    path = Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3"))
    if args.health:
        raise SystemExit(0 if healthy(path) else 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path.parent.mkdir(parents=True, exist_ok=True)
    # flock is fast locally; the DB lease also spans native/Docker filesystems.
    with (path.parent / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Another Still worker holds this data directory.\n")
        owner = str(uuid.uuid4())
        try:
            claim(path, owner)
        except RuntimeError as failure:
            parser.exit(1, f"{failure}.\n")
        try:
            asyncio.run(serve(
                path,
                os.environ.get("STILL_MODEL_BASE_URL", "http://127.0.0.1:8080/v1"),
                os.environ.get("STILL_MODEL", "still"),
                owner,
            ))
        finally:
            release(path, owner)


if __name__ == "__main__":
    main()
