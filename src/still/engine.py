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
from still.world import READING_NAMES, book_title, delivery_description, job_details, personal_threads, snapshot


MODEL_ERRORS = (URLError, OSError, ValueError, KeyError, IndexError, TypeError)


def choose(base_url: str, model: str, actions: list[dict], state: dict,
           *, timing: dict | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "choice": {"type": "integer", "enum": list(range(len(actions)))},
            "book_title": {"type": ["string", "null"]},
        },
        "required": ["choice", "book_title"],
        "additionalProperties": False,
    }
    text = complete(base_url, model, [
        {"role": "system", "content": (
            "You are a courier deciding your next action. Each legal action has an explicit choice index. "
            "Return that exact zero-based index: 0 means the first action, not 1. "
            "Compare outstanding deliveries by deadline and their job.purpose: delaying one holds up its recipient's work, and an accepted input can unlock a follow-up parcel. Choose which commitment to serve first, but work is not your whole life. "
            "Use spare time to restore the file, follow its owner lead when a reply is ready, or borrow and read private notes. "
            "follow_owner asks Ivo first and then Mara; replies take time. read_notes reads the borrowed copy before returning it. "
            "Keep return promises, and help a person if a late return has made them refuse another loan. "
            "Do not return borrowed notes unread unless work or a promise requires it. "
            "Ivo and Mara each lend a four-part book about terminal work. When choosing borrow_notes for a book whose title is null in state.books, invent a short, specific title grounded in its subject and terminal world; do not use generic labels such as 'repair notes' or 'shared paths'. "
            "Return book_title as null for already named books and all other actions. For an unnamed book return the title as plain text, at most 80 characters. "
            "Personal actions use time too; choose them deliberately rather than wandering or waiting forever. "
            "Do not wait without a reason. The simulation, not you, decides consequences. "
            "Return only a JSON object containing choice and book_title."
        )},
        {"role": "user", "content": json.dumps({"state": state, "actions": [
            {"choice": index, "action": action} for index, action in enumerate(actions)
        ]})},
    ], response_format={"type": "json_schema", "json_schema": {
        "name": "courier_choice", "strict": True, "schema": schema,
    }}, timing=timing)
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != {"choice", "book_title"}:
        raise ValueError("model must return only a choice and optional book title")
    number, title = result["choice"], result["book_title"]
    if type(number) is not int or not 0 <= number < len(actions):
        raise ValueError("model choice is not a legal action index")
    action = actions[number]
    unnamed = action["kind"] == "borrow_notes" and not state.get("books", {}).get(action["person_id"], {}).get("title")
    if unnamed:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 80 or any(ord(char) < 32 for char in title):
            raise ValueError("borrowed book title must be plain text of at most 80 characters")
        return {**action, "_book_title": title.strip()}
    if title is not None:
        raise ValueError("book title is only valid when naming an unnamed book")
    return actions[number]


def _entry(db, tick_id):
    row = db.scalar(select(Entry).where(Entry.tick_id == tick_id))
    return as_dict(row) if row else None


def _record_model_ms(db, tick_id: int, choice_ms, reply_ms) -> None:
    """Keep measured model round trips with the tick that produced them."""
    measured = {key: value for key, value in (
        ("model_choice_ms", choice_ms), ("model_reply_ms", reply_ms)) if value is not None}
    if not measured:
        return
    row = db.get(Tick, tick_id)
    row.hardware = {**(row.hardware or {}), **measured}


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
        references = set(db.scalars(select(Person.name))) | set(db.scalars(select(Place.name))) | {
            book_title(db, person_id) for person_id in READING_NAMES
        }
        threads = personal_threads(db)
        personal = db.get(PersonalFile, 1)
        if personal:
            references.add(personal.name)
        at = db.get(Tick, result["id"]).at
        for delivery in db.scalars(select(Delivery).where(
            Delivery.status.in_(("queued", "carried")), Delivery.due_at < at,
        ).order_by(Delivery.due_at, Delivery.id).limit(2 - len(threads))):
            description = delivery_description(delivery.id, delivery.contents)
            references.add(description)
            job = job_details(db, delivery.id)
            threads.append(f"The {description} was still undelivered past its deadline at the {delivery.destination.name}. {job.get('purpose', '')}".strip())
        for event in recent + result["events"]:
            if event.get("delivery_id"):
                parcel = db.get(Delivery, event["delivery_id"])
                references.add(delivery_description(event["delivery_id"], parcel.contents if parcel else ""))
    background = "\n".join(event["text"] for event in reversed(recent)) or "None."
    current = "\n".join(event["text"] for event in result["events"] if event.get("kind") not in ("encounter", "first_meeting"))
    unresolved = "\n".join(f"- {thread}" for thread in threads) or "None recorded."
    moment = (f"Unresolved threads (ongoing, not new discoveries):\n{unresolved}\n\n"
              f"Earlier recorded context (not newly happening):\n{background}\n\nCurrent moment:\n{current}")
    return moment, tuple(sorted(references))


def run_tick(path: Path, tick_key: str, base_url: str, model: str,
             *, now: datetime | None = None, hardware: dict | None = None,
             owner: str | None = None, grow: bool = True) -> dict:
    with session(path, readonly=True) as db:
        existing = db.scalar(select(Tick.id).where(Tick.tick_key == tick_key))
        if existing is not None:
            # A retry can read the result, never generate a skipped moment later.
            return {"tick_id": existing, "entry": _entry(db, existing), "error": None}
    error = None
    choice_ms = None
    try:
        state = snapshot(path)
        state["deliveries"] = [parcel for parcel in state["deliveries"] if parcel["status"] in ("queued", "carried")]
        state["now"] = (now or datetime.now(UTC)).isoformat()
        actions = options(path, hardware, now=now)
        timing: dict = {}
        action = dict(actions[0] if len(actions) == 1 else choose(base_url, model, actions, state, timing=timing))
        choice_ms = timing.get("model_reply_ms")
        chosen_book_title = action.pop("_book_title", None)
    except MODEL_ERRORS as failure:
        error = f"choice failed: {failure}"[:1000]
        action, chosen_book_title = {"kind": "wait"}, None
    result = tick(path, tick_key, now or datetime.now(UTC), action, hardware, owner=owner, grow=grow,
                  book_title=chosen_book_title)
    tick_id = result["id"]
    status = "failed" if error else ("writing" if result["publish"] else "silent")
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        claimed = db.execute(insert(Publication).values(
            tick_id=tick_id, status=status, error=error
        ).on_conflict_do_nothing(index_elements=[Publication.tick_id])).rowcount
        # A silent tick still made a real model call; keep the time it measured.
        _record_model_ms(db, tick_id, choice_ms, None)
        if not claimed or status != "writing":
            return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": error}
    try:
        moment, references = writing_moment(path, result)
        timing = {}
        text = generate(base_url, model, moment, references=references, timing=timing)
        reply_ms = timing.get("model_reply_ms")
    except MODEL_ERRORS as failure:
        error = f"writing failed: {failure}"[:1000]
        with session(path, immediate=True) as db:
            require_owner(db, owner)
            _record_model_ms(db, tick_id, choice_ms, None)
            publication = db.get(Publication, tick_id)
            publication.status = "failed"
            publication.error = error
        return {"tick_id": tick_id, "entry": None, "error": error}
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        _record_model_ms(db, tick_id, choice_ms, reply_ms)
        publication = db.get(Publication, tick_id)
        if publication.status != "writing":
            return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": "publication no longer active"}
        if owner is not None:
            db.get(WorkerLease, 1).heartbeat_at = datetime.now(UTC).isoformat(timespec="microseconds")
        db.add(Entry(tick_id=tick_id, at=result["at"], text=text))
        publication.status = "published"
        return {"tick_id": tick_id, "entry": _entry(db, tick_id), "error": None}
