import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from still.db import session
from still.models import Courier
from still.viewer import diary
from still.world import snapshot


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3"))
    # Only the worker initializes or advances the world.
    with session(path, readonly=True) as db:
        if db.get(Courier, 1) is None:
            raise RuntimeError("Start the Still worker before the viewer")
    app.state.db_path = path
    yield


app = FastAPI(
    title="Still",
    description="World time follows real time in UTC. All timestamps are UTC.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index(before: int | None = Query(default=None, ge=1, le=9223372036854775807)) -> HTMLResponse:
    return diary(app.state.db_path, before)


@app.get("/health")
def health() -> dict[str, str]:
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
