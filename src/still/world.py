"""Persistent starting world; the model never owns these facts."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from still.clock import timestamp
from still.db import as_dict, create_tables, session
from still.models import Courier, Delivery, PersonalFile, PersonalLife, Person, Place, Relationship


READING_NAMES = {"ivo": "repair-notes", "mara": "shared-paths"}

# The route is not finished. A place and the person who keeps it are added one at a time as
# work reaches them, in this order. Each is introduced by the round that first goes there.
WORLD_GROWTH = (
    ("bindery", "Bindery", "establishment", "nadia", "Nadia", "binder"),
    ("goods-yard", "Goods yard", "depot", "oskar", "Oskar", "shunter"),
)
# Rounds between one new place and the next, counted in finished deliveries.
GROWTH_INTERVAL = 6


def _initialize_life(db) -> None:
    db.flush()
    if db.get(PersonalLife, 1) is None:
        db.add(PersonalLife(id=1))
    for person_id in READING_NAMES:
        if db.get(Person, person_id) is not None and db.get(Relationship, person_id) is None:
            db.add(Relationship(person_id=person_id))


def personal_threads(db) -> list[str]:
    threads = []
    personal = db.get(PersonalFile, 1)
    life = db.get(PersonalLife, 1)
    if personal and ("owner: [blank]" in personal.contents or "[unreadable]" in personal.contents):
        text = (f"The owner field in my {personal.name} file was still blank." if "owner: [blank]" in personal.contents
                else f"My {personal.name} file still had unreadable lines.")
        text += f" Known contents:\n{personal.contents.rstrip()}"
        if life and life.owner_stage in (1, 3):
            person = "Ivo" if life.owner_stage == 1 else "Mara"
            text += f"\n{person} was checking an older index; a reply was expected after {datetime.fromisoformat(life.reply_at):%d %b %Y at %H:%M} UTC."
        elif life and life.owner_stage == 2:
            text += "\nIvo found a shared-path reference, but I had not asked Mara about it."
        threads.append(text)
    if life and life.borrowed_from:
        person = db.get(Person, life.borrowed_from)
        text = f"I owed {READING_NAMES[person.id]} back to {person.name} by {datetime.fromisoformat(life.return_due_at):%d %b %Y at %H:%M} UTC."
        text += f" My reading: {life.reading_note}" if life.read_during_loan else " I had not opened it yet."
        threads.append(text)
    for relationship in db.scalars(select(Relationship).where(Relationship.needs_help == 1).order_by(Relationship.person_id)):
        person = db.get(Person, relationship.person_id)
        threads.append(f"After my late return, {person.name} wanted help checking the shared path before lending again.")
    for memory in db.scalars(select(Relationship).where(Relationship.last_note != "").order_by(Relationship.person_id)):
        if not life or life.borrowed_from != memory.person_id:
            threads.append(f"My last reading in {READING_NAMES[memory.person_id]}: {memory.last_note}")
    return threads[:2]


def delivery_description(identifier: str) -> str:
    # shortcut: derive contents from legacy IDs; store explicit contents when deliveries gain richer metadata.
    match = re.fullmatch(r"parcel-([1-9][0-9]*)", identifier, re.IGNORECASE)
    if match is None:
        return identifier
    number = int(match[1])
    description = ("corrected directory index", "access request", "sealed directory copy")[(number - 1) % 3]
    return description if number <= 3 else f"{description} {number}"


def initialize(path: Path, now: datetime) -> None:
    created_at = timestamp(now)
    with session(path, immediate=True) as db:
        create_tables(db)
        if db.get(PersonalFile, 1) is None:
            db.add(PersonalFile(id=1, name="old-index", progress=0,
                                contents="[unreadable]\n[unreadable]\n[unreadable]\n", shown_progress=-1))
        if db.get(Courier, 1) is not None:
            _initialize_life(db)
            return
        db.add_all([
            Place(id="depot", name="Depot", kind="depot"),
            Place(id="records-office", name="Records office", kind="office"),
            Place(id="repair-shop", name="Repair shop", kind="establishment"),
        ])
        db.flush()
        db.add_all([
            Person(id="mara", name="Mara", role="receptionist", place_id="records-office"),
            Person(id="ivo", name="Ivo", role="repairer", place_id="repair-shop"),
        ])
        db.flush()
        db.add(Courier(id=1, place_id="depot", started_at=created_at))
        db.add_all([
            Delivery(id="parcel-1", sender_id="ivo", recipient_id="mara",
                     origin_id="repair-shop", destination_id="records-office",
                     created_at=created_at, due_at=timestamp(now + timedelta(hours=4))),
            Delivery(id="parcel-2", sender_id="mara", recipient_id="ivo",
                     origin_id="records-office", destination_id="repair-shop",
                     created_at=created_at, due_at=timestamp(now + timedelta(hours=6))),
        ])
        _initialize_life(db)


def snapshot(path: Path) -> dict:
    with session(path, readonly=True) as db:
        return {
            "timezone": "UTC",
            "courier": as_dict(db.get(Courier, 1)),
            "personal_file": as_dict(db.get(PersonalFile, 1)),
            "personal_life": as_dict(db.get(PersonalLife, 1)),
            "personal_threads": personal_threads(db),
            "relationships": [as_dict(row) for row in db.scalars(select(Relationship).order_by(Relationship.person_id))],
            "places": [as_dict(row) for row in db.scalars(select(Place).order_by(Place.id))],
            "people": [as_dict(row) for row in db.scalars(select(Person).order_by(Person.id))],
            "deliveries": [{**as_dict(row), "description": delivery_description(row.id)} for row in db.scalars(
                select(Delivery).order_by(Delivery.due_at, Delivery.id)
            )],
        }
