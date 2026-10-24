"""One model-backed tick. No web request calls this module."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

from still.prose import complete, generate
from still.simulation import options, tick
from still.world import snapshot


MODEL_ERRORS = (URLError, OSError, ValueError, KeyError, IndexError, TypeError)


def choose(base_url: str, model: str, actions: list[dict], state: dict) -> dict:
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "integer", "enum": list(range(len(actions)))}},
        "required": ["choice"],
        "additionalProperties": False,
    }
    text = complete(base_url, model, [
        {"role": "system", "content": (
            "You are a courier deciding your next action. Choose one numbered legal action. "
            "Try to finish outstanding deliveries before their deadlines. "
            "Do not wait without a reason. The simulation, not you, decides consequences. "
            "Return only a JSON object containing the integer choice."
        )},
        {"role": "user", "content": json.dumps({"state": state, "actions": actions})},
    ], response_format={"type": "json_schema", "json_schema": {
        "name": "courier_choice", "strict": True, "schema": schema,
    }})
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != {"choice"}:
        raise ValueError("model must return only a choice")
    number = result["choice"]
    if type(number) is not int or not 0 <= number < len(actions):
        raise ValueError("model choice is not a legal action index")
    return actions[number]


def _entry(connection, tick_id):
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM entries WHERE tick_id = ?", (tick_id,)).fetchone()
    return dict(row) if row else None


def run_tick(path: Path, tick_key: str, base_url: str, model: str,
             *, now: datetime | None = None, hardware: dict | None = None) -> dict:
    with closing(sqlite3.connect(path)) as connection:
        existing = connection.execute("SELECT id FROM ticks WHERE tick_key = ?", (tick_key,)).fetchone()
        if existing:
            # A retry can read the result, never generate a skipped moment later.
            return {"tick_id": existing[0], "entry": _entry(connection, existing[0]), "error": None}
    error = None
    try:
        state = snapshot(path)
        state["now"] = (now or datetime.now(UTC)).isoformat()
        action = choose(base_url, model, options(path), state)
    except MODEL_ERRORS as failure:
        error = f"choice failed: {failure}"[:1000]
        action = {"kind": "wait"}
    result = tick(path, tick_key, now or datetime.now(UTC), action, hardware)
    tick_id = result["id"]
    status = "failed" if error else ("writing" if result["publish"] else "silent")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        claimed = connection.execute(
            "INSERT OR IGNORE INTO publications (tick_id, status, error) VALUES (?, ?, ?)",
            (tick_id, status, error),
        ).rowcount
        if not claimed or status != "writing":
            return {"tick_id": tick_id, "entry": _entry(connection, tick_id), "error": error}
    try:
        text = generate(base_url, model, "Facts from this moment only:\n" + "\n".join(
            event["text"] for event in result["events"]
        ))
    except MODEL_ERRORS as failure:
        error = f"writing failed: {failure}"[:1000]
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE publications SET status = 'failed', error = ? WHERE tick_id = ?",
                               (error, tick_id))
        return {"tick_id": tick_id, "entry": None, "error": error}
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO entries (tick_id, at, text) VALUES (?, ?, ?)",
                           (tick_id, result["at"], text))
        connection.execute("UPDATE publications SET status = 'published' WHERE tick_id = ?", (tick_id,))
        return {"tick_id": tick_id, "entry": _entry(connection, tick_id), "error": None}
