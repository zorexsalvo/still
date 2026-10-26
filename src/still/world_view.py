"""Read-only world overview, separate from the model and worker."""

import logging
import re
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from still.db import session
from still.simulation import withdrawal
from still.models import (
    Arrival, BackfillTick, Courier, Delivery, Encounter, Event,
    PersonalFile, PersonalLife, Person, Place, Relationship, Tick, WorkerState, WorldClock,
)
from still.viewer import _page, _utc, host_status, presence
from still.world import book_title, delivery_description, job_details, personal_threads


# Travel, collection, handover and meetings are steps, not changes: the delivery table already
# shows route and status, and the people list already counts encounters. This feed exists to show
# what actually changed, so it deliberately omits those routine kinds.
CHANGE_KINDS = (
    "work_arrived", "work_enabled", "work_cancelled", "delivery_refused", "new_place", "deadline_missed", "inspection", "owner_followup",
    "file_recovered", "file_completed", "personal_visit", "personal_borrow", "personal_read",
    "personal_return", "personal_routine", "personal_refusal", "personal_help", "personal_deadline_missed",
)


log = logging.getLogger(__name__)


def _time(value: str) -> str:
    at = _utc(value)
    return f'<time datetime="{at.isoformat()}">{at:%d %b %Y · %H:%M} UTC</time>'


def _parcel_label(delivery: Delivery) -> str:
    description = delivery_description(delivery.id, delivery.contents)
    number = re.fullmatch(r"parcel-([1-9][0-9]*)", delivery.id, re.IGNORECASE)
    if not delivery.contents and number:
        description = re.sub(r" \d+$", "", description)
    description = description.title()
    return f'{description} · Parcel {number[1]}' if number else description


def world_page(path: Path, *, now: datetime | None = None, before: int | None = None) -> HTMLResponse:
    now = now or datetime.now(UTC)
    try:
        with session(path, readonly=True) as db:
            courier = db.get(Courier, 1)
            if courier is None:
                raise ValueError("world is not initialized")
            places = list(db.scalars(select(Place).order_by(Place.id)))
            names = {place.id: place.name for place in places}
            people = list(db.scalars(select(Person).order_by(Person.name)))
            occasional = set(db.scalars(select(Arrival.person_id).distinct()))
            encounters = {row.person_id: row for row in db.scalars(select(Encounter))}
            personal = db.get(PersonalFile, 1)
            life = db.get(PersonalLife, 1)
            relationships = {row.person_id: row for row in db.scalars(select(Relationship))}
            worker = db.get(WorkerState, 1)
            clock = db.get(WorldClock, 1)
            carried = db.scalar(select(Delivery).where(Delivery.status == "carried"))
            deliveries = list(db.scalars(select(Delivery).order_by(
                Delivery.created_at.desc(), Delivery.id
            ).limit(50)))
            activity_query = select(Event, Tick.at, BackfillTick.generated_at).join(
                Tick, Tick.id == Event.tick_id
            ).outerjoin(BackfillTick, BackfillTick.tick_id == Tick.id).where(
                Event.kind.in_(CHANGE_KINDS)
            ).order_by(Tick.at.desc(), Event.id.desc()).limit(9)
            if before is not None:
                cursor = db.get(Event, before)
                if cursor is None:
                    activity_query = activity_query.where(Event.id < 0)
                else:
                    cursor_at = db.scalar(select(Tick.at).where(Tick.id == cursor.tick_id))
                    activity_query = activity_query.where(tuple_(Tick.at, Event.id) < (cursor_at, before))
            activity = list(db.execute(activity_query))
            parts = [
                '<div class="reading-heading"><h2>World</h2><a href="/world/view">Refresh world</a></div>',
                '<p class="world-note">The recorded state behind the diary. Nothing here asks for a new entry.</p>',
                '<section class="world-section"><h3>Courier</h3><dl class="world-facts">',
                f'<dt>Location</dt><dd>{escape(names.get(courier.place_id, courier.place_id))}</dd>',
                f'<dt>Carrying</dt><dd>{"<svg class=\"parcel-icon\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"m3 7 9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10M8 5l9 4\"/></svg>" if carried else ""}{escape(_parcel_label(carried)) if carried else "Nothing"}</dd>',
                f'<dt>Withdrawal</dt><dd>{withdrawal(db, now=now)}</dd>',
                f'<dt>Borrowed notes</dt><dd>{escape(book_title(db, life.borrowed_from)) + " · return due " + _time(life.return_due_at) if life and life.borrowed_from else "Nothing"}</dd>',
                f'<dt>Life started</dt><dd>{_time(courier.started_at)}</dd>',
                f'<dt>Next tick</dt><dd>{"In progress" if worker and worker.active_tick_key else (_time(worker.next_tick_at) if worker else "Not scheduled yet")}</dd>',
                f'<dt>Last check-in</dt><dd>{_time(clock.last_seen) if clock else "Not recorded yet"}</dd>',
                '</dl>',
            ]
            if clock is None:
                parts.append('<p class="world-note">The worker has not checked in yet.</p>')
            elif not -5 <= (now - _utc(clock.last_seen)).total_seconds() <= 90:
                parts.append('<p class="world-note">The worker has not checked in recently. Showing its last recorded state.</p>')
            parts.append('</section><section class="world-section"><h3>Personal file</h3>')
            if personal:
                state = "Recovered" if personal.progress == 3 else f"{personal.progress} of 3 lines recovered"
                shared = "Decoded file shown to Ivo" if personal.shown_progress == personal.progress else "Decoded file not yet shown to Ivo"
                parts.extend([
                    f'<figure class="world-file"><figcaption><strong>{escape(personal.name)}</strong> · {state}</figcaption>',
                    f'<pre>{escape(personal.contents)}</pre></figure><p class="world-note">{shared}.</p>',
                ])
            else:
                parts.append('<p class="world-note">No personal file has been started.</p>')
            threads = personal_threads(db)
            if threads:
                parts.append('<h4>Open threads</h4><ul>')
                parts.extend(f'<li>{escape(thread)}</li>' for thread in threads)
                parts.append('</ul>')
            parts.append('</section><section class="world-section"><h3>Places and people</h3><ul class="world-places" role="list">')
            for place in places:
                parts.append(f'<li><h4>{escape(place.name)}</h4>')
                regulars = [person for person in people if person.place_id == place.id and person.id not in occasional]
                if not regulars:
                    parts.append('<p class="world-note">No regulars recorded here.</p>')
                for person in regulars:
                    parts.append(f'<p><strong>{escape(person.name)}</strong> · {escape(person.role)}</p>')
                    memory = relationships.get(person.id)
                    if memory and memory.loans:
                        parts.append(f'<p class="world-note">Notes returned: {memory.returns}; late: {memory.late_returns}.</p>')
                        if memory.needs_help:
                            parts.append('<p class="world-note">Wants help checking the shared path before lending again.</p>')
                    encounter = encounters.get(person.id)
                    if encounter:
                        parts.append(f'<p class="world-note">{encounter.count} encounters. Last seen {_time(encounter.last_at)}.</p>')
                parts.append('</li>')
            parts.append('</ul>')
            visitors = [person for person in people if person.id in occasional]
            if visitors:
                parts.append('<h4>Occasional visitors</h4><ul class="world-visitors">')
                for person in visitors:
                    parts.append(f'<li>{escape(person.name)} · {escape(person.role)}. Not permanent staff.</li>')
                parts.append('</ul>')
            parts.append('</section><section class="world-section"><h3>Parcel deliveries</h3>')
            if deliveries:
                parts.append('<p class="world-note">Packages carried between places. This table records their route, current status and deadline.</p><p class="world-note table-hint">Scroll sideways for status and due times.</p><div class="table-scroll" role="region" aria-label="Parcel deliveries, scroll horizontally if needed" tabindex="0"><table><caption>Up to 50 most recent parcels</caption><thead><tr><th scope="col">Contents</th><th scope="col">Route</th><th scope="col">Status</th><th scope="col">Due UTC</th></tr></thead><tbody>')
                status_labels = {"queued": "Queued", "carried": "Being carried", "delivered": "Delivered", "returned": "Returned", "lost": "Lost"}
                for delivery in deliveries:
                    purpose = job_details(db, delivery.id).get("purpose", "") if delivery.status in ("queued", "carried") else ""
                    purpose_html = f'<p class="world-note">{escape(purpose)}</p>' if purpose else ""
                    parts.append(
                        f'<tr><td>{escape(_parcel_label(delivery))}{purpose_html}</td>'
                        f'<td>{escape(names.get(delivery.origin_id, delivery.origin_id))}<br>to {escape(names.get(delivery.destination_id, delivery.destination_id))}</td>'
                        f'<td>{escape(status_labels.get(delivery.status, delivery.status))}</td><td>{_time(delivery.due_at)}</td></tr>'
                    )
                parts.append('</tbody></table></div>')
            else:
                parts.append('<p class="world-note">No deliveries recorded.</p>')
            parts.append('</section><section class="world-section"><h3>Recent changes</h3>')
            if before is not None:
                parts.append('<p class="world-note"><a href="/world/view">Latest changes</a></p>')
            if activity:
                parts.append('<ol class="world-activity" role="list">')
                for index, (event, at, generated) in enumerate(activity[:8]):
                    if generated and (index == 0 or activity[index - 1][2] != generated):
                        parts.append(f'<li class="world-backfill"><p class="world-note">Backdated: the changes below were generated {_time(generated)}, not recorded live.</p></li>')
                    parts.append(f'<li>{_time(at)}<p>{escape(event.facts["text"])}</p></li>')
                parts.append('</ol>')
                if len(activity) > 8:
                    parts.append(f'<nav class="pagination" aria-label="Recent changes pages"><a href="/world/view?before={activity[7][0].id}" rel="next">Older changes</a></nav>')
                parts.append('<p class="world-note">Routine travel, collection and handover are not listed here; the parcel table shows route and status.</p>')
            else:
                parts.append('<p class="world-note">No changes recorded yet. Quiet ticks do not appear here.</p>')
            parts.append('</section>')
            return _page("".join(parts), now, world=True, ambient=presence(path, now), footer_extra=host_status(path))
    except (SQLAlchemyError, ValueError):
        log.exception("Unable to read the world")
        return _page('<section class="empty"><h2>The world could not be loaded.</h2><p>Please try again in a moment.</p><a href="/world/view">Try again</a></section>', now, status=503, world=True)
