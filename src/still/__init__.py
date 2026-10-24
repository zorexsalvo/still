import os
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

import uvicorn
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get("STILL_DB_PATH", "data/still.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    app.state.db_path = path
    yield


app = FastAPI(title="Still", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    with closing(sqlite3.connect(app.state.db_path)) as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ok"}


def main() -> None:
    uvicorn.run("still:app", host="0.0.0.0", port=8000)
