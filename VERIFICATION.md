# Verification and deployment

## Running platforms

- **Mac:** development and the detailed automated, inference and browser checks recorded below.
- **Raspberry Pi:** tested and now running, as confirmed by the project author. Pi hardware specifications, inference placement, benchmark results and sustained-run duration have not been recorded here.

The Pi deployment confirmation does not retroactively make the Mac test counts or measurements Pi results. It also does not establish a multi-day soak, Pi-specific load-threshold calibration or thermal behavior.

## Mac development baseline

Originally verified on 2026-10-24, macOS arm64 with OrbStack, native llama.cpp 0.7.0, and `ggml-org/gemma-4-E2B-it-GGUF` Q4_0 served under the `still` alias. The dated development evidence below comes from this Mac environment unless otherwise noted.

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

## Continuing private life — 2026-10-25

**97 tests pass** natively and in Docker. Added coverage for 18-hour reply availability using replay time rather than host time, state-preserving initialization, owner-field changes, persistent loans and reading positions, single overdue-promise events, late-return refusal/help, work delaying a personal return, borrowing cooldowns, quiet rereading, hardware restrictions and invalid-state constraints. The real-Gemma temporary-world smoke still passes publication, HTTP/offline reading, replay and restart-rest checks.

The World page exposes borrowed notes, UTC return time, current private threads and lending history using its existing document components. Isolated loan fixtures were rendered and inspected at 1440px and 390px; no page overflow. Captures: `.impeccable/review/continuing-world-desktop.png` and `continuing-world-mobile.png`. No Jev dependency or call was added.

## Minimal courier presence — 2026-10-25

**110 tests pass** natively and in Docker. Static inline SVG scenes use the latest recorded tick for arrival, reading/restoration, waiting and actual interaction partners; companions disappear on a later solo tick, and older imported ticks do not override newer moments. Inspections and refused loans do not depict a successful handoff. Read-only footer status uses stored courier location, worker check-in, completed live runs with matching persisted ticks, and the next eligible time. Replay ticks and unfinished/orphan runs are not counted as completed live progress. Recent, stale, future-clock, active and failed states are tested; raw worker errors are not exposed, and unavailable status does not hide an otherwise readable diary.

Chrome checked the diary and World page at 1440px and 390px with isolated synthetic fixtures, including stale/error state and two recorded locations. No page overflow, prose overlap or animation. Evidence: `.impeccable/review/presence-*.png`. These are review captures, not shipping assets. The courier SVG is authored inline; no raster asset, external request, polling or inference is added. Static detection reported no findings; it is not a full semantic/accessibility audit. Live data and worker processes were not changed for this UI work, and the remote deployment was not verified.

## Limits of this evidence

Hourly cadence and long gaps were tested deterministically rather than by waiting a full week. Pi operation is confirmed separately above, but no multi-day soak duration, Pi-specific performance measurements or thermal calibration are documented here. Neither deployment nor these checks guarantee future model factuality or prose quality. The starter world can still produce long silences under its intentionally simple writing gate. See README for operational limits and deferred features.
