"""Persistent starting world; the model never owns these facts."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from still.clock import timestamp
from still.db import as_dict, create_tables, session
from still.models import Courier, Delivery, Event, PersonalFile, PersonalLife, Person, Place, Relationship


READING_NAMES = {"ivo": "The Misaligned Index", "mara": "The Directory Without an Owner"}


def named_book_title(db, person_id: str) -> str | None:
    for facts in db.scalars(select(Event.facts).where(Event.kind == "personal_borrow").order_by(Event.id.desc())):
        if facts.get("person_id") == person_id and facts.get("book_title"):
            return facts["book_title"]
    return None


def book_title(db, person_id: str) -> str:
    return named_book_title(db, person_id) or READING_NAMES[person_id]

# What a round can be carrying, cycled by parcel number so consecutive rounds do not repeat.
CONTENTS = (
    "corrected directory index", "access request", "sealed directory copy", "blank label sheet",
    "signed transfer form", "unopened invoice bundle", "spine repair order", "returned key tag",
)

# The route is not finished. A place and the person who keeps it are added one at a time as
# work reaches them, in this order. Each is introduced by the round that first goes there.
WORLD_GROWTH = (
    ("bindery", "Bindery", "establishment", "nadia", "Nadia", "binder", "mara"),
    ("goods-yard", "Goods yard", "depot", "oskar", "Oskar", "shunter", "nadia"),
    ("print-room", "Print room", "establishment", "elin", "Elin", "printer", "oskar"),
    ("ferry-depot", "Ferry depot", "depot", "laleh", "Laleh", "ferry keeper", "elin"),
    ("map-room", "Map room", "office", "tomas", "Tomas", "surveyor", "laleh"),
)
# Rounds between one new place and the next, counted in finished deliveries.
GROWTH_INTERVAL = 6

# Input, activity and output for each regular's work; these are world facts, not model inventions.
WORK = {
    "ivo": ("access request", "restore a damaged index", "restored index"),
    "mara": ("corrected directory index", "reopen the directory register", "countersigned register"),
    "nadia": ("spine repair order", "bind the loose directory volumes", "bound directory volume"),
    "oskar": ("signed transfer form", "release the queued directory copies", "dispatch manifest"),
    "elin": ("blank label sheet", "print replacement directory labels", "printed label bundle"),
    "laleh": ("returned key tag", "open the locked transfer queue", "transfer receipt"),
    "tomas": ("sealed directory copy", "revise the route index", "revised route map"),
}


def job_details(db, delivery_id: str) -> dict:
    return db.scalar(select(Event.facts).where(
        Event.kind == "work_arrived", Event.facts["delivery_id"].as_string() == delivery_id
    ).order_by(Event.id).limit(1)) or {}


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
        text = f"I owed {book_title(db, person.id)} back to {person.name} by {datetime.fromisoformat(life.return_due_at):%d %b %Y at %H:%M} UTC."
        text += f" My reading: {life.reading_note}" if life.read_during_loan else " I had not opened it yet."
        threads.append(text)
    for relationship in db.scalars(select(Relationship).where(Relationship.needs_help == 1).order_by(Relationship.person_id)):
        person = db.get(Person, relationship.person_id)
        threads.append(f"After my late return, {person.name} wanted help checking the shared path before lending again.")
    for memory in db.scalars(select(Relationship).where(Relationship.last_note != "").order_by(Relationship.person_id)):
        if not life or life.borrowed_from != memory.person_id:
            threads.append(f"My last reading in {book_title(db, memory.person_id)}: {memory.last_note}")
    return threads[:2]


def delivery_description(identifier: str, contents: str = "") -> str:
    """What a round is carrying. Rounds store their own contents; older rows fall back to their id."""
    if contents:
        return contents
    match = re.fullmatch(r"parcel-([1-9][0-9]*)", identifier, re.IGNORECASE)
    if match is None:
        return identifier
    number = int(match[1])
    description = CONTENTS[(number - 1) % len(CONTENTS)]
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
            "books": {
                person_id: {
                    "title": named_book_title(db, person_id),
                    "subject": subject,
                }
                for person_id, subject in {
                    "ivo": "An incomplete repair path, corrected lines beside old versions, undated failures, and an earlier copy whose location is missing.",
                    "mara": "An unnamed directory entry, a reference to a removed directory, two unmarked versions, and an unanswered question about deletion permissions.",
                }.items()
            },
            "relationships": [as_dict(row) for row in db.scalars(select(Relationship).order_by(Relationship.person_id))],
            "places": [as_dict(row) for row in db.scalars(select(Place).order_by(Place.id))],
            "people": [as_dict(row) for row in db.scalars(select(Person).order_by(Person.id))],
            "deliveries": [{**as_dict(row), "description": delivery_description(row.id, row.contents),
                            **({"job": job_details(db, row.id)} if row.status in ("queued", "carried") else {})}
                           for row in db.scalars(
                select(Delivery).order_by(Delivery.due_at, Delivery.id)
            )],
        }
