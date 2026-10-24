# Local MVP verification

Verified on 2026-10-24, macOS arm64 with OrbStack, native llama.cpp 0.7.0, and `ggml-org/gemma-4-E2B-it-GGUF` Q4_0 served under the `still` alias.

## Automated checks

- **46 tests pass** natively and inside the Python 3.12 Docker image.
- Coverage includes legal/illegal actions, atomic rollback, duplicate tick keys, concurrent duplicates, real UTC boundaries, restart scheduling, no downtime backfill, lease takeover/fencing, model failure/malformed output, immutable entries, escaped HTML, pagination, and read-only access.
- A pre-ORM fixture preserves every existing row and table/trigger definition across SQLAlchemy initialization.
- `uv lock --check`, `docker compose config --quiet`, container builds, and wheel packaging pass. The wheel includes the viewer stylesheet.

```sh
uv run python -W error::ResourceWarning -m unittest discover -s tests

docker compose run --rm --no-deps \
  -v "$PWD/tests:/app/tests:ro" --entrypoint python still \
  -W error::ResourceWarning -m unittest discover -s tests
```

## Real inference and persistence

`uv run python tests/live_smoke.py` passed against native Gemma: a legal move changed courier state, first meeting facts produced an immutable entry, and a temporary FastAPI server returned that entry over HTTP. Inference was then made unreachable; the diary stayed readable and replay returned the same entry. Restart initialization preserved the saved completion-plus-one-hour rest.

Representative output:

> I went to the Repair shop. I met Ivo at the Repair shop.

The actual Compose worker independently produced this same first-meeting entry in the live database using a fresh Darwin host sample. Repeated service rebuilds/restarts preserved courier location, parcel states, entry text, tick count, and the next eligible time. Stopping the worker left the HTTP diary readable. SQLite integrity and foreign-key checks passed. No live clock or saved deadline was changed to speed up verification.

A native second worker was initially found to bypass Docker's `flock`; the added database lease now rejects that launch. Rechecked against the running Docker worker, not only mocked in unit tests.

## Browser checks

Chrome headless checked 1440px desktop and 390px mobile, plus isolated, labelled synthetic fixtures for empty/error/gap/overlong states. Keyboard skip/focus, entry permalinks, earlier/latest navigation, wrapping, invalid cursor rejection, and rejection of POST requests passed. No horizontal overflow was found. The mechanical design detector returned no findings; inline visual review recorded `ship` for the inspected baseline. No independent visual subagent was available.

Text contrast: 12.58:1 primary, 6.03:1 secondary, 7.99:1 links. Captures and the review are retained locally under `.impeccable/review/`, not shipped as assets. Test browser/server processes were stopped afterward.

## Writing gate and unresolved threads — 2026-10-24

The revised gate keeps successful deliveries and first introductions silent, even when the private file is unresolved. Tests cover lateness/inspection eligibility, a maximum of two state-derived threads, persistence of the blank owner after decoding, removal of resolved items, receipt stripping, repeated names/noun phrases, and the requested system-language bans. The current suite passes **87 tests** natively and in Docker.

The updated isolated real-Gemma smoke passed with a finished-round fixture. The model chose `restore_file` and wrote:

> I opened my old-index file. I recovered the path /archive/landing. The owner line was still unreadable.

Publication, HTTP reading with inference unavailable, replay, and restart/rest preservation passed. The live diary was not changed by these tests. The lexical guards are not evidence of general writing quality or semantic factuality.

## Limits of this evidence

Hourly cadence and long gaps are tested deterministically rather than by waiting a full week. This is not a multi-day soak, a guarantee of future model factuality, a bespoke visual-brand approval, or Raspberry Pi performance validation. The starter route can produce long or indefinite silence under its intentionally simple writing gate. See README for operational limits and deferred features.
