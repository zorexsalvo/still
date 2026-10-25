"""One model-backed tick. No web request calls this module."""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from still.db import as_dict, require_owner, session
from still.models import BackfillTick, Delivery, Entry, Event, PersonalFile, Person, Place, Publication, Tick, WorkerLease
from still.prose import complete, generate
from still.simulation import options, tick
from still.world import READING_NAMES, delivery_description, personal_threads, snapshot


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
            "You are a courier deciding your next action. Each legal action has an explicit choice index. "
            "Return that exact zero-based index: 0 means the first action, not 1. "
            "Try to finish outstanding deliveries before their deadlines, but work is not your whole life. "
            "Use spare time to restore the file, follow its owner lead when a reply is ready, or borrow and read private notes. "
            "follow_owner asks Ivo first and then Mara; replies take time. read_notes reads the borrowed copy before returning it. "
            "Keep return promises, and help a person if a late return has made them refuse another loan. "
            "Do not return borrowed notes unread unless work or a promise requires it. "
            "Ivo lends repair-notes and Mara lends shared-paths; each has four passages. Prefer unread notes over rereading the same ones. "
            "Personal actions use time too; choose them deliberately rather than wandering or waiting forever. "
            "Do not wait without a reason. The simulation, not you, decides consequences. "
            "Return only a JSON object containing the integer choice."
        )},
        {"role": "user", "content": json.dumps({"state": state, "actions": [
            {"choice": index, "action": action} for index, action in enumerate(actions)
        ]})},
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


def _entry(db, tick_id):
    row = db.scalar(select(Entry).where(Entry.tick_id == tick_id))
    return as_dict(row) if row else None


def writing_moment(path: Path, result: dict) -> tuple[str, tuple[str, ...]]:
    with session(path, readonly=True) as db:
        last_entry = db.scalar(select(Entry.tick_id).where(
            Entry.tick_id.not_in(select(BackfillTick.tick_id))
        ).order_by(Entry.at.desc(), Entry.id.desc()).limit(1)) or 0
        recent = list(db.scalars(select(Event.facts).where(
            Event.tick_id > last_entry,
            Event.tick_id < result["id"],
            Event.kind.not_in(("waited", "encounter", "first_meeting")),
            Event.tick_id.not_in(select(BackfillTick.tick_id)),
        ).order_by(Event.id.desc()).limit(8)))
        references = set(db.scalars(select(Person.name))) | set(db.scalars(select(Place.name))) | set(READING_NAMES.values())
        threads = personal_threads(db)
        personal = db.get(PersonalFile, 1)
        if personal:
            references.add(personal.name)
        at = db.get(Tick, result["id"]).at
        for delivery in db.scalars(select(Delivery).where(
            Delivery.status.in_(("queued", "carried")), Delivery.due_at < at,
        ).order_by(Delivery.due_at, Delivery.id).limit(2 - len(threads))):
            description = delivery_description(delivery.id)
            references.add(description)
            threads.append(f"The {description} was still undelivered past its deadline at the {delivery.destination.name}.")
        for event in recent + result["events"]:
            if event.get("delivery_id"):
                references.add(delivery_description(event["delivery_id"]))
    background = "\n".join(event["text"] for event in reversed(recent)) or "None."
    current = "\n".join(event["text"] for event in result["events"] if event.get("kind") not in ("encounter", "first_meeting"))
    unresolved = "\n".join(f"- {thread}" for thread in threads) or "None recorded."
    moment = (f"Unresolved threads (ongoing, not new discoveries):\n{unresolved}\n\n"
              f"Earlier recorded context (not newly happening):\n{background}\n\nCurrent moment:\n{current}")
    return moment, tuple(sorted(references))


def run_tick(path: Path, tick_key: str, base_url: str, model: str,
             *, now: datetime | None = None, hardware: dict | None = None,
             owner: str | None = None) -> dict:
    with session(path, readonly=True) as db:
        existing = db.scalar(select(Tick.id).where(Tick.tick_key == tick_key))
        if existing is not None:
            # A retry can read the result, never generate a skipped moment later.
            return {"tick_id": existing, "entry": _entry(db, existing), "error": None}
    error = None
    try:
        state = snapshot(path)
        state["now"] = (now or datetime.now(UTC)).isoformat()
        actions = options(path, hardware, now=now)
        action = actions[0] if len(actions) == 1 else choose(base_url, model, actions, state)
    except MODEL_ERRORS as failure:
        error = f"choice failed: {failure}"[:1000]
        action = {"kind": "wait"}
    result = tick(path, tick_key, now or datetime.now(UTC), action, hardware, owner=owner)
    tick_id = result["id"]
    status = "failed" if error else ("writing" if result["publish"] else "silent")
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        claimed = db.execute(insert(Publication).values(
            tick_id=tick_id, status=status, error=error
        ).on_conflict_do_nothing(index_elements=[Publication.tick_id])).rowcount
        if not claimed or status != "writing":
            return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": error}
    try:
        moment, references = writing_moment(path, result)
        text = generate(base_url, model, moment, references=references)
    except MODEL_ERRORS as failure:
        error = f"writing failed: {failure}"[:1000]
        with session(path, immediate=True) as db:
            require_owner(db, owner)
            publication = db.get(Publication, tick_id)
            publication.status = "failed"
            publication.error = error
        return {"tick_id": tick_id, "entry": None, "error": error}
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        publication = db.get(Publication, tick_id)
        if publication.status != "writing":
            return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": "publication no longer active"}
        if owner is not None:
            db.get(WorkerLease, 1).heartbeat_at = datetime.now(UTC).isoformat(timespec="microseconds")
        db.add(Entry(tick_id=tick_id, at=result["at"], text=text))
        publication.status = "published"
        return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": None}
