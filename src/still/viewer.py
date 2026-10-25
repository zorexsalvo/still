"""Read-only HTML chronicle. This module never imports or calls inference."""

import logging
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from still.db import as_dict, session
from still.models import BackfillTick, Courier, Entry, Event, Person, Place, Tick, WorkerRun, WorkerState, WorldClock


PAGE_SIZE = 50
log = logging.getLogger(__name__)
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def published(path: Path, before: int | None = None) -> list[dict]:
    query = select(Entry, BackfillTick.generated_at).outerjoin(
        BackfillTick, BackfillTick.tick_id == Entry.tick_id
    ).order_by(Entry.at.desc(), Entry.id.desc()).limit(PAGE_SIZE + 1)
    with session(path, readonly=True) as db:
        if before is not None:
            cursor = db.get(Entry, before)
            if cursor is None:
                return []
            query = query.where(tuple_(Entry.at, Entry.id) < (cursor.at, cursor.id))
        return [{**as_dict(entry), "backdated_at": generated_at} for entry, generated_at in db.execute(query)]


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("entry timestamp must include timezone")
    return result.astimezone(UTC)


def _gap(newer: datetime, older: datetime) -> str:
    days = (newer.date() - older.date()).days - 1
    if days < 1:
        return ""
    unit = "day" if days == 1 else "days"
    return f'<p class="gap">{days} full UTC {unit} without an entry.</p>'


def _scene(db, place) -> str:
    moment = db.scalar(select(Tick).order_by(Tick.at.desc(), Tick.id.desc()).limit(1))
    action = moment.action if moment and isinstance(moment.action, dict) else {}
    kind = action.get("kind")
    events = list(db.scalars(select(Event).where(Event.tick_id == moment.id))) if moment else []
    kinds = {event.kind for event in events}
    person_id = action.get("person_id") if kind in {"visit", "follow_owner", "borrow_notes", "return_notes", "help_person"} else None
    for event in events:
        if event.kind == "inspection":
            person_id = event.facts.get("person_id")
            break
        if event.kind == "delivered":
            person_id = event.facts.get("recipient_id")
    person = db.get(Person, person_id) if person_id else None
    pose = {"move": "walking", "read_notes": "reading", "restore_file": "reading"}.get(kind, "standing")
    label = {"move": "Still arriving", "read_notes": "Still reading", "restore_file": "Still restoring the file", "wait": "Still waiting"}.get(kind, "Still")
    if person:
        label = f"Still with {person.name}"
    if "personal_refusal" in kinds or "delivery_refused" in kinds:
        label = f"Still with {person.name} · request refused" if person else "Still · request refused"
    position = {"depot": "depot", "records-office": "records", "repair-shop": "repair"}.get(place.id, "unknown")
    head = '<path d="M23 19c0-7 3-11 9-11s9 4 9 11M20 19h24M25 21v5c0 5 3 8 7 8s7-3 7-8v-5"/>'
    coat = '<path d="M24 36l-7 7-3 25 7 2 3-17v27h16V53l3 17 7-2-3-25-7-7M24 38l18 22M35 58h16v16H35zM35 63h16"/>'
    legs = '<path d="M27 81l-2 10h-6M37 81l2 10h6"/>'
    if pose == "walking":
        legs = '<path d="M28 81l-9 8-6 2M36 81l10 7 6 3"/>'
    if pose == "reading":
        coat = '<path d="M24 36l-5 15 8 13h14l7 21M27 64l-10 9 12 15M24 39l10 12M34 43l12 3 12-3v19l-12 3-12-3zM46 46v19"/>'
        legs = ''
    companion = '<path class="scene-place" d="M144 94V22h34v72M170 57h2"/>'
    if person:
        companion = '<g class="scene-companion" transform="translate(120 4)"><circle cx="32" cy="22" r="8"/><path d="M24 36l-8 9-3 24M40 36l8 9 3 24M24 36v42h16V36M27 79l-2 12h-5M37 79l2 12h5"/></g>'
        handoff = (kind in {"borrow_notes", "return_notes"} or "delivered" in kinds) and not kinds & {"personal_refusal", "delivery_refused", "inspection"}
        if handoff:
            companion += '<path class="scene-handoff" d="M58 57h27M101 57h34M85 48h16v20H85zM89 53h8M89 58h6"/>'
    recorded = f"Recorded {_utc(moment.at):%d %b %Y · %H:%M} UTC" if moment else "Recorded location"
    context = "recorded moment" if moment else "recorded location"
    return f'''<div class="ambient-world"><figure class="courier-presence presence-{position} scene-{pose}" title="{recorded}">
<svg viewBox="0 0 200 106" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" focusable="false" aria-hidden="true">
<path d="M6 99h182"/><g class="scene-still" transform="translate(8 4)">{head}{coat}{legs}</g>{companion}
</svg><figcaption>{escape(label)}<span>{escape(place.name)} · {context}</span></figcaption></figure></div>'''


def presence(path: Path, now: datetime) -> tuple[str, str]:
    """A saved location and operational evidence, never an inference request."""
    try:
        with session(path, readonly=True) as db:
            courier = db.get(Courier, 1)
            place = db.get(Place, courier.place_id) if courier else None
            clock = db.get(WorldClock, 1)
            state = db.get(WorkerState, 1)
            last = db.scalar(select(WorkerRun).where(WorkerRun.finished_at.is_not(None)).order_by(WorkerRun.finished_at.desc()).limit(1))
            completed = db.scalar(select(WorkerRun.finished_at).join(Tick, Tick.tick_key == WorkerRun.tick_key).where(
                WorkerRun.finished_at.is_not(None), WorkerRun.error.is_(None)
            ).order_by(WorkerRun.finished_at.desc()).limit(1))
            # A heartbeat may commit just after the page's as-of timestamp.
            age = (now - _utc(clock.last_seen)).total_seconds() if clock else None
            if age is None:
                checkin = "No worker check-in yet"
            elif age < -5:
                checkin = "Worker check-in time is in the future"
            else:
                checkin = "Worker check-in recent" if age <= 90 else "Worker check-in stale"
            if clock:
                checkin += f' ({_status_time(clock.last_seen)})'
            details = [f'<span>At {escape(place.name)}</span>'] if place else ['<span>Location not recorded</span>']
            details.append(f'<span>{checkin}</span>')
            details.append(f'<span>Last completed live tick: {_status_time(completed) if completed else "none yet"}</span>')
            if state and state.active_tick_key:
                details.append('<span>Tick marked in progress</span>')
            else:
                details.append(f'<span>Next eligible tick: {_status_time(state.next_tick_at) if state else "not scheduled"}</span>')
            if last and last.error is not None:
                details.append('<span>Last finished attempt had an error · see worker logs</span>')
            return (_scene(db, place) if place else "",
                    '<aside class="life-status" aria-label="Recorded world status">' + "".join(details) + '</aside>')
    except (SQLAlchemyError, ValueError):
        log.warning("Unable to read courier presence", exc_info=True)
        return "", '<aside class="life-status" aria-label="Recorded world status">World status unavailable · <a href="/world/view">Check world</a></aside>'


def _status_time(value: str) -> str:
    at = _utc(value)
    return f'<time datetime="{at.isoformat()}">{at:%d %b %Y · %H:%M}&#160;UTC</time>'


def _page(content: str, now: datetime, *, status: int = 200, world: bool = False, ambient: tuple[str, str] = ("", "")) -> HTMLResponse:
    section = "world" if world else "diary"
    title = "Still. the world" if world else "Still. a courier’s diary"
    diary_current = '' if world else ' aria-current="page"'
    world_current = ' aria-current="page"' if world else ''
    footer = "Read-only. Viewing the world never advances it." if world else "Entries are occasional. Refreshing does not ask for one."
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="One courier’s diary, written as life happens. All times UTC.">
  <link rel="stylesheet" href="/static/diary.css">
  <link rel="icon" href="/static/favicon.ico" sizes="16x16 32x32 48x48">
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml" sizes="any">
</head>
<body>
  <a class="skip-link" href="#{section}">Skip to {section}</a>
  <header class="page-header">
    <div><h1><a href="/">Still</a></h1><p>A courier’s diary.</p></div>
    <p class="clock">All times UTC<br>As of <time datetime="{now.isoformat()}">{now:%d %b %Y · %H:%M} UTC</time></p>
  </header>
  <nav class="section-nav" aria-label="Sections"><a href="/"{diary_current}>Diary</a><a href="/world/view"{world_current}>World</a></nav>
  {ambient[0]}
  <main id="{section}" tabindex="-1">{content}</main>
  <footer class="page-footer"><p>{footer}</p>{ambient[1]}</footer>
</body>
</html>""", status_code=status, headers=HEADERS)


def diary(path: Path, before: int | None = None, *, now: datetime | None = None) -> HTMLResponse:
    now = now or datetime.now(UTC)
    try:
        entries = published(path, before)
        content = '<div class="reading-heading"><h2>Earlier entries</h2><a href="/">Latest entries</a></div>' if before else (
            '<div class="reading-heading"><h2>Latest entries</h2><a href="/">Refresh entries</a></div>'
        )
        if not entries:
            content += '<section class="empty"><h3>No entries on this page.</h3><p>Return to the <a href="/">latest entries</a>.</p></section>' if before else (
                '<section class="empty"><h3>No entries yet.</h3><p>Writing is occasional. There is nothing to read yet.</p></section>'
            )
        else:
            if before is None:
                content += _gap(now, _utc(entries[0]["at"]))
            for index, entry in enumerate(entries[:PAGE_SIZE]):
                at = _utc(entry["at"])
                identifier = entry["id"]
                content += f'''<article class="entry" id="entry-{identifier}" aria-labelledby="date-{identifier}">
<h3 id="date-{identifier}"><a href="#entry-{identifier}"><time datetime="{at.isoformat()}">{at:%d %B %Y · %H:%M} UTC</time></a></h3>
<p class="entry-text">{escape(entry["text"])}</p>'''
                if entry["backdated_at"]:
                    generated = _utc(entry["backdated_at"])
                    content += f'<p class="entry-origin">Backdated · generated {generated:%d %B %Y · %H:%M} UTC, not recorded live.</p>'
                content += '</article>'
                if index + 1 < len(entries):
                    content += _gap(at, _utc(entries[index + 1]["at"]))
            if len(entries) > PAGE_SIZE:
                cursor = entries[PAGE_SIZE - 1]["id"]
                content += f'<nav class="pagination" aria-label="Diary pages"><a href="/?before={cursor}" rel="next">Earlier entries</a></nav>'
        return _page(content, now, ambient=presence(path, now))
    except (SQLAlchemyError, ValueError):
        log.exception("Unable to read the published diary")
        return _page('<section class="empty"><h2>The diary could not be loaded.</h2><p>Please try again in a moment.</p><a href="/">Try again</a></section>', now, status=503)
