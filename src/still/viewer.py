"""Read-only HTML chronicle. This module never imports or calls inference."""

import logging
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from still.db import as_dict, session
from still.models import BackfillTick, Entry


PAGE_SIZE = 50
log = logging.getLogger(__name__)
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
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


def _page(content: str, now: datetime, *, status: int = 200, world: bool = False) -> HTMLResponse:
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
</head>
<body>
  <a class="skip-link" href="#{section}">Skip to {section}</a>
  <header class="page-header">
    <div><h1><a href="/">Still</a></h1><p>A courier’s diary.</p></div>
    <p class="clock">All times UTC<br>As of <time datetime="{now.isoformat()}">{now:%d %b %Y · %H:%M} UTC</time></p>
  </header>
  <nav class="section-nav" aria-label="Sections"><a href="/"{diary_current}>Diary</a><a href="/world/view"{world_current}>World</a></nav>
  <main id="{section}" tabindex="-1">{content}</main>
  <footer class="page-footer"><p>{footer}</p></footer>
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
        return _page(content, now)
    except (SQLAlchemyError, ValueError):
        log.exception("Unable to read the published diary")
        return _page('<section class="empty"><h2>The diary could not be loaded.</h2><p>Please try again in a moment.</p><a href="/">Try again</a></section>', now, status=503)
