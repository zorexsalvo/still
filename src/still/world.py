"""Persistent starting world; the model never owns these facts."""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from still.clock import timestamp
from still.db import as_dict, create_tables, session
from still.models import Courier, Delivery, Person, Place


def initialize(path: Path, now: datetime) -> None:
    created_at = timestamp(now)
    with session(path, immediate=True) as db:
        create_tables(db)
        if db.get(Courier, 1) is not None:
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


def snapshot(path: Path) -> dict:
    with session(path, readonly=True) as db:
        return {
            "timezone": "UTC",
            "courier": as_dict(db.get(Courier, 1)),
            "places": [as_dict(row) for row in db.scalars(select(Place).order_by(Place.id))],
            "people": [as_dict(row) for row in db.scalars(select(Person).order_by(Person.id))],
            "deliveries": [as_dict(row) for row in db.scalars(
                select(Delivery).order_by(Delivery.due_at, Delivery.id)
            )],
        }
