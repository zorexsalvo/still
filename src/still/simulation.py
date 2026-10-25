"""Legal courier actions and their atomic, deterministic consequences."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from still.clock import timestamp
from still.db import require_owner, session
from still.hardware import under_pressure
from still.models import Arrival, Courier, Delivery, Encounter, Event, PersonalFile, PersonalLife, Person, Place, Relationship, Tick, WorkerLease
from still.world import CONTENTS, GROWTH_INTERVAL, READING_NAMES, WORLD_GROWTH, delivery_description


WRITING_EVENTS = {"deadline_missed", "delivery_failed", "delivery_refused", "inspection", "file_recovered", "file_completed", "personal_visit",
                  "owner_followup", "personal_borrow", "personal_read", "personal_return", "personal_deadline_missed", "personal_refusal", "personal_help"}
# A handover offered this long after its deadline is refused outright rather than accepted late.
REFUSAL_AFTER = timedelta(hours=12)
READINGS = {
    "ivo": (
        "An early repair note stopped halfway through a path. Space had been left for the missing part.",
        "A corrected line was followed by the earlier version. Neither had been removed.",
        "The last two entries in a list of failed repairs had no dates beside them.",
        "The final note said to keep the earlier copy. It did not say where it had been kept.",
    ),
    "mara": (
        "The first shared-path entry had no name beside it. A space for one had been left.",
        "A later entry pointed back to a directory that was no longer in the list.",
        "Two versions of an entry had been kept. Neither was marked as the newer one.",
        "The last entry ended with a question about who could remove it. There was no answer.",
    ),
}
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
    "I showed Ivo the file I'd recovered. He looked at the empty owner field. He handed it back without filling it in.",
)


def legal_actions(db: Session, hardware: dict | None = None, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
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
    life = db.get(PersonalLife, 1)
    if personal.progress == 3 and personal.shown_progress == 3:
        if life.owner_stage < 4 and (life.reply_at is None or datetime.fromisoformat(life.reply_at) <= now):
            person = db.get(Person, "ivo" if life.owner_stage < 2 else "mara")
            if person.place_id == place or (carried is None and not local_pickup):
                actions.append({"kind": "follow_owner", "person_id": person.id})
        if life.owner_stage == 4:
            if life.borrowed_from:
                if not life.read_during_loan:
                    actions.append({"kind": "read_notes"})
                person = db.get(Person, life.borrowed_from)
                if person.place_id == place or (carried is None and not local_pickup):
                    actions.append({"kind": "return_notes", "person_id": person.id})
            for person_id in READING_NAMES:
                person = db.get(Person, person_id)
                memory = db.get(Relationship, person_id)
                if person.place_id != place and (carried is not None or local_pickup):
                    continue
                if memory.needs_help and memory.refused_at:
                    actions.append({"kind": "help_person", "person_id": person_id})
                elif not life.borrowed_from and not memory.refused_at and (
                    memory.last_returned_at is None or now >= datetime.fromisoformat(memory.last_returned_at) + timedelta(hours=24)
                ):
                    actions.append({"kind": "borrow_notes", "person_id": person_id})
    if under_pressure(hardware or {}):
        actions = [action for action in actions if action["kind"] == "deliver"]
    return actions + [{"kind": "wait"}]


def options(path: Path, hardware: dict | None = None, *, now: datetime | None = None) -> list[dict]:
    with session(path, readonly=True) as db:
        return legal_actions(db, hardware, now=now)


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
         *, owner: str | None = None, grow: bool = True) -> dict:
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
        if action not in legal_actions(db, hardware, now=now):
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
                       f"The deadline for the {delivery_description(parcel.id, parcel.contents)} at the {parcel.destination.name} had passed. It was still undelivered.",
                       event_key=event_key, delivery_id=parcel.id)

        life = db.get(PersonalLife, 1)
        if life.borrowed_from and at > life.return_due_at:
            memory = db.get(Relationship, life.borrowed_from)
            key = f"personal-overdue:{memory.person_id}:{memory.loans}"
            if db.scalar(select(Event.id).where(Event.event_key == key)) is None:
                person = db.get(Person, memory.person_id)
                _event(db, tick_id, "personal_deadline_missed",
                       f"I still had {person.name}'s borrowed notes after the time I had promised to return them.",
                       event_key=key, person_id=person.id, file_name=READING_NAMES[person.id])

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
            _event(db, tick_id, "collected", f"I collected the {delivery_description(parcel.id, parcel.contents)}.", delivery_id=parcel.id)
        elif kind == "deliver":
            parcel = db.get(Delivery, action["delivery_id"])
            if at > timestamp(datetime.fromisoformat(parcel.due_at) + REFUSAL_AFTER):
                # Too late to be worth taking. The handover does not happen at all, so there is
                # nothing for the inspector to check.
                parcel.status = "returned"
                _event(db, tick_id, "delivery_refused",
                       f"{parcel.recipient.name} would not take the {delivery_description(parcel.id, parcel.contents)}. It was too late to hand over, so I carried it back.",
                       delivery_id=parcel.id, recipient_id=parcel.recipient_id)
            else:
                parcel.status = "delivered"
                _event(db, tick_id, "delivered", f"I delivered the {delivery_description(parcel.id, parcel.contents)} to {parcel.recipient.name}. They accepted it and signed for it.",
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
                           f"Ren came to check the late handoff of the {delivery_description(parcel.id, parcel.contents)}, wrote down the time, and left.",
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
        elif kind == "read_notes":
            memory = db.get(Relationship, life.borrowed_from)
            passages = READINGS[memory.person_id]
            novel = memory.read_count < len(passages)
            life.reading_note = passages[memory.read_count % len(passages)]
            memory.last_note = life.reading_note
            life.read_during_loan = 1
            memory.read_count += 1
            title = READING_NAMES[memory.person_id]
            verb = "read" if novel else "reread"
            _event(db, tick_id, "personal_read" if novel else "personal_routine",
                   f"I {verb} {title}. {life.reading_note}", file_name=title, person_id=memory.person_id)
        elif kind in {"follow_owner", "borrow_notes", "return_notes", "help_person"}:
            person = db.get(Person, action["person_id"])
            memory = db.get(Relationship, person.id)
            db.get(Courier, 1).place_id = person.place_id
            _encounter(db, tick_id, person, person.place.name, at)
            title = READING_NAMES[person.id]
            event_kind = "personal_routine"
            if kind == "follow_owner":
                personal = db.get(PersonalFile, 1)
                if life.owner_stage == 0:
                    text = "I asked Ivo about the empty owner field. He kept the path to compare with an older index. He asked me to come back after he had checked it."
                    life.reply_at = timestamp(now + timedelta(hours=18))
                elif life.owner_stage == 1:
                    text = "Ivo found the path under a shared-directory heading. There was no person's name beside it. I kept the reference to ask about at the Records office."
                    personal.contents += "reference: shared\n"
                    life.reply_at = None
                elif life.owner_stage == 2:
                    text = "I asked Mara about the shared-directory reference. She left my question beside an older register to compare when it was free."
                    life.reply_at = timestamp(now + timedelta(hours=18))
                else:
                    text = "Mara showed me the older register. The path was shared, with no individual owner listed. I wrote shared in my empty owner field."
                    personal.contents = personal.contents.replace("owner: [blank]", "owner: shared")
                    life.reply_at = None
                life.owner_stage += 1
                event_kind = "owner_followup"
            elif kind == "borrow_notes":
                if memory.needs_help:
                    text = f"I asked {person.name} to lend the notes again. They kept them back because I had returned the last copy late. They wanted help checking the shared path first."
                    memory.refused_at = at
                    event_kind = "personal_refusal"
                else:
                    memory.loans += 1
                    life.borrowed_from, life.borrowed_at = person.id, at
                    life.return_due_at = timestamp(now + timedelta(hours=24))
                    life.read_during_loan, life.reading_note = 0, ""
                    text = f"I borrowed {title} from {person.name}. They left their place marked and asked for it back the following day."
                    event_kind = "personal_borrow" if memory.loans == 1 else "personal_routine"
            elif kind == "return_notes":
                late = at > life.return_due_at
                memory.returns += 1
                if late:
                    memory.late_returns += 1
                    memory.needs_help = 1
                    text = f"I returned {title} to {person.name} after the time I had promised. They put it away without offering another copy."
                elif life.read_during_loan:
                    text = f"I returned {title} to {person.name}. They kept my place marked for the next time."
                else:
                    text = f"I returned {title} to {person.name} without opening it. They left the old bookmark where it was."
                if late or memory.returns == 1:
                    event_kind = "personal_return"
                memory.last_returned_at = at
                life.borrowed_from = life.borrowed_at = life.return_due_at = None
                life.read_during_loan, life.reading_note = 0, ""
                life.last_returned_at = at
            else:
                text = f"I compared /archive/landing with the index for {person.name}. They let me borrow again after we checked the reference together."
                memory.needs_help, memory.refused_at = 0, None
                event_kind = "personal_help"
            _event(db, tick_id, event_kind, text, person_id=person.id,
                   file_name=personal.name if kind == "follow_owner" else title)
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
            growth = next((entry for entry in WORLD_GROWTH if db.get(Place, entry[0]) is None), None)
            last_growth = db.scalar(select(Tick.at).join(Event, Event.tick_id == Tick.id).where(
                Event.kind == "new_place"
            ).order_by(Tick.at.desc()).limit(1))
            opened = db.scalar(select(func.count()).select_from(Delivery).where(
                Delivery.created_at > last_growth
            )) if last_growth else GROWTH_INTERVAL
            if grow and growth and opened >= GROWTH_INTERVAL:
                # Work reaches a place the route has not covered before, and the person who keeps it.
                place_id, place_name, place_kind, person_id, person_name, role = growth
                db.add(Place(id=place_id, name=place_name, kind=place_kind))
                db.flush()
                db.add(Person(id=person_id, name=person_name, role=role, place_id=place_id))
                db.flush()
                sender, recipient, origin, destination = person_id, "mara", place_id, "records-office"
                _event(db, tick_id, "new_place",
                       f"Work reached the {place_name} for the first time. {person_name} kept the counter there.",
                       place_id=place_id, person_id=person_id)
            else:
                sender, recipient, origin, destination = (
                    ("ivo", "mara", "repair-shop", "records-office") if count % 2 == 0
                    else ("mara", "ivo", "records-office", "repair-shop")
                )
            identifier = f"parcel-{tick_id + 2}"
            load = CONTENTS[count % len(CONTENTS)]
            db.add(Delivery(id=identifier, sender_id=sender, recipient_id=recipient,
                            origin_id=origin, destination_id=destination, created_at=at,
                            due_at=timestamp(now + timedelta(hours=6)), contents=load))
            _event(db, tick_id, "work_arrived", f"The {load} was waiting on the route.",
                   delivery_id=identifier)
        kinds = set(db.scalars(select(Event.kind).where(Event.tick_id == tick_id)))
        current.publish = int(bool(kinds & WRITING_EVENTS))
        return _result(db, tick_id)
