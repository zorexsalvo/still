import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from sqlalchemy import select

from still.clock import checkpoint, resume
from still.db import session
from still.world import initialize, snapshot


async def heartbeat(path: Path) -> None:
    while True:
        # shortcut: crashes may add up to 30 seconds to downtime; use a supervisor's stop time if needed.
        await asyncio.sleep(30)
        checkpoint(path, datetime.now(UTC))


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    initialize(path, now)
    resume(path, now)
    app.state.db_path = path
    task = asyncio.create_task(heartbeat(path))
    app.state.heartbeat = task
    try:
        yield
    finally:
        task.cancel()
        try:
            with suppress(asyncio.CancelledError):
                await task
        finally:
            checkpoint(path, datetime.now(UTC))


app = FastAPI(
    title="Still",
    description="World time follows real time in UTC. All timestamps are UTC.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    if app.state.heartbeat.done():
        raise RuntimeError("world clock heartbeat stopped")
    with session(app.state.db_path, readonly=True) as db:
        db.scalar(select(1))
    return {"status": "ok"}


@app.get("/clock")
def clock() -> dict[str, str]:
    return {"timezone": "UTC", "now": datetime.now(UTC).isoformat()}


@app.get("/world")
def world() -> dict:
    return snapshot(app.state.db_path)


def main() -> None:
    uvicorn.run("still:app", host="0.0.0.0", port=8000)
