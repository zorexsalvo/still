"""Legal courier actions and their atomic, deterministic consequences."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from still.clock import timestamp


WRITING_EVENTS = {"first_meeting", "deadline_missed", "inspection"}


def legal_actions(connection: sqlite3.Connection) -> list[dict]:
    place = connection.execute("SELECT place_id FROM courier WHERE id = 1").fetchone()[0]
    carried = connection.execute("SELECT id, destination_id FROM deliveries WHERE status = 'carried'").fetchone()
    actions = []
    if carried:
        if carried[1] == place:
            actions.append({"kind": "deliver", "delivery_id": carried[0]})
        else:
            actions.append({"kind": "move", "place_id": carried[1]})
    else:
        destinations = set()
        for parcel, origin in connection.execute(
            "SELECT id, origin_id FROM deliveries WHERE status = 'queued' ORDER BY due_at, id"
        ):
            if origin == place:
                actions.append({"kind": "collect", "delivery_id": parcel})
            else:
                destinations.add(origin)
        actions.extend({"kind": "move", "place_id": destination} for destination in sorted(destinations))
    return actions + [{"kind": "wait"}]


def options(path: Path) -> list[dict]:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN")
        return legal_actions(connection)


def _event(connection, tick_id, kind, text, *, event_key=None, **facts):
    connection.execute(
        "INSERT INTO events (tick_id, kind, facts, event_key) VALUES (?, ?, ?, ?)",
        (tick_id, kind, json.dumps({"text": text, **facts}), event_key),
    )


def _encounter(connection, tick_id, person, place, at):
    first = connection.execute(
        "SELECT 1 FROM encounters WHERE person_id = ?", (person[0],)
    ).fetchone() is None
    connection.execute(
        "INSERT INTO encounters (person_id, first_at, last_at, count) VALUES (?, ?, ?, 1) "
        "ON CONFLICT (person_id) DO UPDATE SET last_at = excluded.last_at, count = count + 1",
        (person[0], at, at),
    )
    _event(connection, tick_id, "first_meeting" if first else "encounter",
           f"I met {person[1]} at the {place}.", person_id=person[0])


def _result(connection, tick_id):
    row = connection.execute(
        "SELECT id, at, action, hardware, publish FROM ticks WHERE id = ?", (tick_id,)
    ).fetchone()
    return {
        "id": row[0], "at": row[1], "action": json.loads(row[2]),
        "hardware": json.loads(row[3]), "publish": bool(row[4]),
        "events": [
            {"kind": kind, **json.loads(facts)}
            for kind, facts in connection.execute(
                "SELECT kind, facts FROM events WHERE tick_id = ? ORDER BY id", (tick_id,)
            )
        ],
    }


def tick(path: Path, tick_key: str, now: datetime, action: dict, hardware: dict | None = None) -> dict:
    """Apply one legal action. Reusing a tick key returns its frozen result."""
    at = timestamp(now)
    if not isinstance(tick_key, str) or not tick_key.strip():
        raise ValueError("tick key must be nonempty")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT id FROM ticks WHERE tick_key = ?", (tick_key,)).fetchone()
        if existing:
            return _result(connection, existing[0])
        last = connection.execute("SELECT at FROM ticks ORDER BY id DESC LIMIT 1").fetchone()
        if last and at < last[0]:
            raise ValueError("tick time cannot run backwards")
        if action not in legal_actions(connection):
            raise ValueError("illegal courier action")
        cursor = connection.execute(
            "INSERT INTO ticks (tick_key, at, action, hardware) VALUES (?, ?, ?, ?)",
            (tick_key, at, json.dumps(action), json.dumps(hardware or {})),
        )
        tick_id = cursor.lastrowid
        for parcel, destination in connection.execute(
            "SELECT id, destination_id FROM deliveries WHERE status IN ('queued', 'carried') AND due_at < ?",
            (at,),
        ).fetchall():
            event_key = f"late:{parcel}"
            if not connection.execute("SELECT 1 FROM events WHERE event_key = ?", (event_key,)).fetchone():
                name = connection.execute("SELECT name FROM places WHERE id = ?", (destination,)).fetchone()[0]
                _event(connection, tick_id, "deadline_missed",
                       f"The deadline for {parcel} at the {name} had passed. It was still undelivered.",
                       event_key=event_key, delivery_id=parcel)

        kind = action["kind"]
        if kind == "move":
            connection.execute("UPDATE courier SET place_id = ? WHERE id = 1", (action["place_id"],))
            name = connection.execute("SELECT name FROM places WHERE id = ?", (action["place_id"],)).fetchone()[0]
            _event(connection, tick_id, "moved", f"I went to the {name}.")
            for person in connection.execute(
                "SELECT id, name FROM people WHERE place_id = ? "
                "AND id NOT IN (SELECT person_id FROM arrivals) ORDER BY id", (action["place_id"],)
            ).fetchall():
                _encounter(connection, tick_id, person, name, at)
        elif kind == "collect":
            parcel = action["delivery_id"]
            connection.execute("UPDATE deliveries SET status = 'carried' WHERE id = ?", (parcel,))
            _event(connection, tick_id, "collected", f"I collected {parcel}.", delivery_id=parcel)
        elif kind == "deliver":
            parcel = action["delivery_id"]
            recipient, name, destination, due_at = connection.execute(
                "SELECT p.id, p.name, d.destination_id, d.due_at FROM deliveries d "
                "JOIN people p ON p.id = d.recipient_id WHERE d.id = ?", (parcel,)
            ).fetchone()
            connection.execute("UPDATE deliveries SET status = 'delivered' WHERE id = ?", (parcel,))
            _event(connection, tick_id, "delivered", f"{name} accepted {parcel} and signed for it.",
                   delivery_id=parcel, recipient_id=recipient)
            if at > due_at:
                connection.execute(
                    "INSERT INTO people (id, name, role, place_id) VALUES ('inspector-ren', 'Ren', 'inspector', ?) "
                    "ON CONFLICT (id) DO UPDATE SET place_id = excluded.place_id", (destination,)
                )
                connection.execute("INSERT INTO arrivals (tick_id, person_id) VALUES (?, 'inspector-ren')", (tick_id,))
                _event(connection, tick_id, "inspection",
                       f"Ren came to check the late handoff of {parcel}, wrote down the time, and left.",
                       person_id="inspector-ren", delivery_id=parcel)
                place = connection.execute("SELECT name FROM places WHERE id = ?", (destination,)).fetchone()[0]
                _encounter(connection, tick_id, ("inspector-ren", "Ren"), place, at)
        else:
            _event(connection, tick_id, "waited", "I stayed where I was for a while.")

        active = connection.execute("SELECT 1 FROM deliveries WHERE status IN ('queued', 'carried')").fetchone()
        if not active:
            # A route needs fresh work, not an independently simulated postal network.
            count = connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
            sender, recipient, origin, destination = (
                ("ivo", "mara", "repair-shop", "records-office") if count % 2 == 0
                else ("mara", "ivo", "records-office", "repair-shop")
            )
            connection.execute(
                "INSERT INTO deliveries (id, sender_id, recipient_id, origin_id, destination_id, created_at, due_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"parcel-{tick_id + 2}", sender, recipient, origin, destination, at, timestamp(now + timedelta(hours=6))),
            )
            _event(connection, tick_id, "work_arrived", "Another package was waiting on the route.")
        kinds = {row[0] for row in connection.execute("SELECT kind FROM events WHERE tick_id = ?", (tick_id,))}
        connection.execute("UPDATE ticks SET publish = ? WHERE id = ?", (bool(kinds & WRITING_EVENTS), tick_id))
        return _result(connection, tick_id)
