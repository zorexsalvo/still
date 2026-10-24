"""Legal courier actions and their atomic, deterministic consequences."""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from still.clock import timestamp
from still.db import require_owner, session
from still.hardware import under_pressure
from still.models import Arrival, Courier, Delivery, Encounter, Event, PersonalFile, Person, Place, Tick, WorkerLease
from still.world import delivery_description


WRITING_EVENTS = {"first_meeting", "deadline_missed", "inspection", "file_recovered", "file_completed", "personal_visit"}
ACTIVE_STATUSES = ("queued", "carried")
FILE_RECOVERY = (
    ("path: /archive/landing\nowner: [unreadable]\nkeep: [unreadable]\n",
     "I opened my {name} file. I recovered the path /archive/landing. The owner line was still unreadable."),
    ("path: /archive/landing\nowner: [blank]\nkeep: [unreadable]\n",
     "I recovered the owner line in {name}. The field after it was empty. I left it empty."),
    ("path: /archive/landing\nowner: [blank]\nkeep: yes\n",
     "I recovered the last line in {name}. It said keep: yes. I saved it with the owner still blank."),
)
FILE_VISITS = (
    "I showed Ivo my {name} file. He could not read its damaged lines. He handed it back.",
    "I showed Ivo /archive/landing in {name}. He could not find the path in his index. I kept my copy.",
    "I showed Ivo the empty owner field in {name}. He asked for the missing name. I had only the empty field.",
    "I brought the recovered {name} to Ivo. He looked at the empty owner field. He handed it back without filling it in.",
)


def legal_actions(db: Session, hardware: dict | None = None) -> list[dict]:
    place = db.get(Courier, 1).place_id
    carried = db.scalar(select(Delivery).where(Delivery.status == "carried"))
    actions = []
    if carried:
        if carried.destination_id == place:
            actions.append({"kind": "deliver", "delivery_id": carried.id})
        else:
            actions.append({"kind": "move", "place_id": carried.destination_id})
    else:
        destinations = set()
        for parcel in db.scalars(select(Delivery).where(Delivery.status == "queued").order_by(Delivery.due_at, Delivery.id)):
            if parcel.origin_id == place:
                actions.append({"kind": "collect", "delivery_id": parcel.id})
            else:
                destinations.add(parcel.origin_id)
        # Finish a pickup at this stop before travelling to another one.
        if not actions:
            actions.extend({"kind": "move", "place_id": destination} for destination in sorted(destinations))
    personal = db.get(PersonalFile, 1)
    local_pickup = any(action["kind"] == "collect" for action in actions)
    if personal.progress < len(FILE_RECOVERY):
        actions.append({"kind": "restore_file"})
    ivo = db.get(Person, "ivo")
    if personal.shown_progress < personal.progress and (
        ivo.place_id == place or (carried is None and not local_pickup)
    ):
        actions.append({"kind": "visit", "person_id": "ivo"})
    if under_pressure(hardware or {}):
        actions = [action for action in actions if action["kind"] == "deliver"]
    return actions + [{"kind": "wait"}]


def options(path: Path, hardware: dict | None = None) -> list[dict]:
    with session(path, readonly=True) as db:
        return legal_actions(db, hardware)


def _event(db, tick_id, kind, text, *, event_key=None, **facts):
    db.add(Event(tick_id=tick_id, kind=kind, facts={"text": text, **facts}, event_key=event_key))


def _encounter(db, tick_id, person, place, at):
    encounter = db.get(Encounter, person.id)
    first = encounter is None
    if first:
        db.add(Encounter(person_id=person.id, first_at=at, last_at=at, count=1))
    else:
        encounter.last_at = at
        encounter.count += 1
    _event(db, tick_id, "first_meeting" if first else "encounter",
           f"I met {person.name} at the {place}.", person_id=person.id)


def _result(db, tick_id):
    row = db.get(Tick, tick_id)
    return {
        "id": row.id, "at": row.at, "action": row.action,
        "hardware": row.hardware, "publish": bool(row.publish),
        "events": [
            {"kind": event.kind, **event.facts}
            for event in db.scalars(select(Event).where(Event.tick_id == tick_id).order_by(Event.id))
        ],
    }


def tick(path: Path, tick_key: str, now: datetime, action: dict, hardware: dict | None = None,
         *, owner: str | None = None) -> dict:
    """Apply one legal action. Reusing a tick key returns its frozen result."""
    at = timestamp(now)
    if not isinstance(tick_key, str) or not tick_key.strip():
        raise ValueError("tick key must be nonempty")
    with session(path, immediate=True) as db:
        require_owner(db, owner)
        if owner is not None:
            db.get(WorkerLease, 1).heartbeat_at = at
        existing = db.scalar(select(Tick).where(Tick.tick_key == tick_key))
        if existing:
            return _result(db, existing.id)
        last = db.scalar(select(Tick).order_by(Tick.at.desc(), Tick.id.desc()).limit(1))
        if last and at < last.at:
            raise ValueError("tick time cannot run backwards")
        if action not in legal_actions(db, hardware):
            raise ValueError("illegal courier action")
        current = Tick(tick_key=tick_key, at=at, action=action, hardware=hardware or {})
        db.add(current)
        db.flush()
        tick_id = current.id
        for parcel in db.scalars(select(Delivery).where(
            Delivery.status.in_(ACTIVE_STATUSES), Delivery.due_at < at
        )).all():
            event_key = f"late:{parcel.id}"
            if db.scalar(select(Event.id).where(Event.event_key == event_key)) is None:
                _event(db, tick_id, "deadline_missed",
                       f"The deadline for the {delivery_description(parcel.id)} at the {parcel.destination.name} had passed. It was still undelivered.",
                       event_key=event_key, delivery_id=parcel.id)

        kind = action["kind"]
        if kind == "move":
            db.get(Courier, 1).place_id = action["place_id"]
            place = db.get(Place, action["place_id"])
            _event(db, tick_id, "moved", f"I went to the {place.name}.")
            for person in db.scalars(select(Person).where(
                Person.place_id == place.id, Person.id.not_in(select(Arrival.person_id))
            ).order_by(Person.id)).all():
                _encounter(db, tick_id, person, place.name, at)
        elif kind == "collect":
            parcel = db.get(Delivery, action["delivery_id"])
            parcel.status = "carried"
            _event(db, tick_id, "collected", f"I collected the {delivery_description(parcel.id)}.", delivery_id=parcel.id)
        elif kind == "deliver":
            parcel = db.get(Delivery, action["delivery_id"])
            parcel.status = "delivered"
            _event(db, tick_id, "delivered", f"{parcel.recipient.name} accepted the {delivery_description(parcel.id)} and signed for it.",
                   delivery_id=parcel.id, recipient_id=parcel.recipient_id)
            if at > parcel.due_at:
                inspector = db.get(Person, "inspector-ren")
                if inspector is None:
                    inspector = Person(id="inspector-ren", name="Ren", role="inspector", place_id=parcel.destination_id)
                    db.add(inspector)
                else:
                    inspector.place_id = parcel.destination_id
                db.flush()
                db.add(Arrival(tick_id=tick_id, person_id=inspector.id))
                _event(db, tick_id, "inspection",
                       f"Ren came to check the late handoff of the {delivery_description(parcel.id)}, wrote down the time, and left.",
                       person_id=inspector.id, delivery_id=parcel.id)
                _encounter(db, tick_id, inspector, parcel.destination.name, at)
        elif kind == "restore_file":
            personal = db.get(PersonalFile, 1)
            contents, observation = FILE_RECOVERY[personal.progress]
            personal.contents = contents
            personal.progress += 1
            event = "file_completed" if personal.progress == len(FILE_RECOVERY) else "file_recovered"
            _event(db, tick_id, event, observation.format(name=personal.name),
                   file_name=personal.name, progress=personal.progress)
        elif kind == "visit":
            personal = db.get(PersonalFile, 1)
            person = db.get(Person, action["person_id"])
            db.get(Courier, 1).place_id = person.place_id
            _encounter(db, tick_id, person, person.place.name, at)
            personal.shown_progress = personal.progress
            _event(db, tick_id, "personal_visit", FILE_VISITS[personal.progress].format(name=personal.name),
                   person_id=person.id, file_name=personal.name, progress=personal.progress)
        else:
            if under_pressure(hardware or {}):
                _event(db, tick_id, "slowed", "Moving was slow. I stayed where I was.")
            else:
                _event(db, tick_id, "waited", "I stayed where I was for a while.")

        active = db.scalar(select(Delivery.id).where(Delivery.status.in_(ACTIVE_STATUSES)).limit(1))
        if active is None and db.scalar(select(Delivery.id).where(
            Delivery.created_at.startswith(at[:10])
        ).limit(1)) is None:
            # An exhausted round stays finished for the day, leaving time for a private life.
            count = db.scalar(select(func.count()).select_from(Delivery))
            sender, recipient, origin, destination = (
                ("ivo", "mara", "repair-shop", "records-office") if count % 2 == 0
                else ("mara", "ivo", "records-office", "repair-shop")
            )
            identifier = f"parcel-{tick_id + 2}"
            db.add(Delivery(id=identifier, sender_id=sender, recipient_id=recipient,
                            origin_id=origin, destination_id=destination, created_at=at,
                            due_at=timestamp(now + timedelta(hours=6))))
            _event(db, tick_id, "work_arrived", f"The {delivery_description(identifier)} was waiting on the route.",
                   delivery_id=identifier)
        kinds = set(db.scalars(select(Event.kind).where(Event.tick_id == tick_id)))
        current.publish = int(bool(kinds & WRITING_EVENTS))
        return _result(db, tick_id)
