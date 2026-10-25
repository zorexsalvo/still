"""Typed mappings for the existing SQLite schema; timestamps remain UTC ISO text."""

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (CheckConstraint("kind IN ('depot', 'office', 'establishment')"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.id"))
    place: Mapped[Place] = relationship()


class Courier(Base):
    __tablename__ = "courier"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.id"))
    started_at: Mapped[str] = mapped_column(Text)


class PersonalFile(Base):
    __tablename__ = "personal_file"
    __table_args__ = (
        CheckConstraint("id = 1"),
        CheckConstraint("progress BETWEEN 0 AND 3"),
        CheckConstraint("shown_progress BETWEEN -1 AND progress"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer)
    contents: Mapped[str] = mapped_column(Text)
    shown_progress: Mapped[int] = mapped_column(Integer)


class PersonalLife(Base):
    __tablename__ = "personal_life"
    __table_args__ = (
        CheckConstraint("id = 1"),
        CheckConstraint("owner_stage BETWEEN 0 AND 4"),
        CheckConstraint("(borrowed_from IS NULL AND borrowed_at IS NULL AND return_due_at IS NULL) OR (borrowed_from IS NOT NULL AND borrowed_at IS NOT NULL AND return_due_at IS NOT NULL AND return_due_at > borrowed_at)"),
        CheckConstraint("read_during_loan IN (0, 1)"),
        CheckConstraint("borrowed_from IS NULL OR owner_stage = 4"),
        CheckConstraint("read_during_loan = 0 OR borrowed_from IS NOT NULL"),
        CheckConstraint("(owner_stage IN (1, 3) AND reply_at IS NOT NULL) OR (owner_stage IN (0, 2, 4) AND reply_at IS NULL)"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_stage: Mapped[int] = mapped_column(Integer, default=0)
    reply_at: Mapped[str | None] = mapped_column(Text)
    borrowed_from: Mapped[str | None] = mapped_column(ForeignKey("people.id"))
    borrowed_at: Mapped[str | None] = mapped_column(Text)
    return_due_at: Mapped[str | None] = mapped_column(Text)
    read_during_loan: Mapped[int] = mapped_column(Integer, default=0)
    reading_note: Mapped[str] = mapped_column(Text, default="")
    last_returned_at: Mapped[str | None] = mapped_column(Text)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint("loans >= 0 AND returns >= 0 AND late_returns >= 0 AND read_count >= 0"),
        CheckConstraint("late_returns <= returns AND returns <= loans AND read_count <= loans"),
        CheckConstraint("needs_help IN (0, 1)"),
    )
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    loans: Mapped[int] = mapped_column(Integer, default=0)
    returns: Mapped[int] = mapped_column(Integer, default=0)
    late_returns: Mapped[int] = mapped_column(Integer, default=0)
    read_count: Mapped[int] = mapped_column(Integer, default=0)
    last_note: Mapped[str] = mapped_column(Text, default="")
    needs_help: Mapped[int] = mapped_column(Integer, default=0)
    refused_at: Mapped[str | None] = mapped_column(Text)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'carried', 'delivered', 'returned', 'lost')"),
        CheckConstraint("origin_id != destination_id"),
        CheckConstraint("due_at > created_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("people.id"))
    recipient_id: Mapped[str] = mapped_column(ForeignKey("people.id"))
    origin_id: Mapped[str] = mapped_column(ForeignKey("places.id"))
    destination_id: Mapped[str] = mapped_column(ForeignKey("places.id"))
    created_at: Mapped[str] = mapped_column(Text)
    due_at: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    sender: Mapped[Person] = relationship(foreign_keys=[sender_id])
    recipient: Mapped[Person] = relationship(foreign_keys=[recipient_id])
    origin: Mapped[Place] = relationship(foreign_keys=[origin_id])
    destination: Mapped[Place] = relationship(foreign_keys=[destination_id])


class WorldClock(Base):
    __tablename__ = "world_clock"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seen: Mapped[str] = mapped_column(Text)


class Downtime(Base):
    __tablename__ = "downtime"
    __table_args__ = (CheckConstraint("kind IN ('sleep', 'mundane')"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[str] = mapped_column(Text)
    ended_at: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)


class WorkerLease(Base):
    __tablename__ = "worker_lease"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(Text)
    heartbeat_at: Mapped[str] = mapped_column(Text)


class WorkerRun(Base):
    __tablename__ = "worker_runs"
    tick_key: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class WorkerState(Base):
    __tablename__ = "worker_state"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_tick_at: Mapped[str] = mapped_column(Text)
    active_tick_key: Mapped[str | None] = mapped_column(ForeignKey("worker_runs.tick_key"))


class Tick(Base):
    __tablename__ = "ticks"
    __table_args__ = (CheckConstraint("publish IN (0, 1)"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick_key: Mapped[str] = mapped_column(Text, unique=True)
    at: Mapped[str] = mapped_column(Text)
    action: Mapped[dict] = mapped_column(JSON)
    hardware: Mapped[dict] = mapped_column(JSON)
    publish: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick_id: Mapped[int] = mapped_column(ForeignKey("ticks.id"))
    kind: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict] = mapped_column(JSON)
    event_key: Mapped[str | None] = mapped_column(Text, unique=True)


class Encounter(Base):
    __tablename__ = "encounters"
    __table_args__ = (CheckConstraint("count > 0"),)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
    first_at: Mapped[str] = mapped_column(Text)
    last_at: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer)


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (CheckConstraint("status IN ('silent', 'writing', 'published', 'failed')"),)
    tick_id: Mapped[int] = mapped_column(ForeignKey("ticks.id"), primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (CheckConstraint("length(trim(text)) > 0"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tick_id: Mapped[int] = mapped_column(ForeignKey("ticks.id"), unique=True)
    at: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)


class BackfillTick(Base):
    __tablename__ = "backfill_ticks"
    tick_id: Mapped[int] = mapped_column(ForeignKey("ticks.id"), primary_key=True)
    day: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)


class Arrival(Base):
    __tablename__ = "arrivals"
    tick_id: Mapped[int] = mapped_column(ForeignKey("ticks.id"), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), primary_key=True)
