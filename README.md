# Still

A courier's diary, written by a local model. Current foundation: FastAPI, persistent SQLite, a UTC world clock, a starting route and delivery queue, and a standalone prose test. Legal simulation actions and model-backed tick publication are implemented; the continuous worker and viewer are next.

## Docker (Mac first)

Start Docker Desktop or OrbStack, then:

```sh
docker compose up --build -d
curl http://localhost:8000/health
```

SQLite is stored at `./data/still.sqlite3`, bind-mounted into `/app/data`.
The entire directory is mounted to keep SQLite's WAL and shared-memory files alongside the database. Rebuilding or removing containers does not delete this directory. Deleting `data/` does delete the database. Do not copy a live database file alone for backups; use SQLite's backup API or stop the app before copying the directory.

The server is exposed only on localhost. API docs: <http://localhost:8000/docs>.

```sh
docker compose logs -f
docker compose down
```

## Without Docker

```sh
uv sync --locked
uv run still
```

`STILL_DB_PATH` defaults to `data/still.sqlite3`.

## Database access

Synchronous SQLAlchemy 2.x models live in `src/still/models.py`. Use the session context manager in `src/still/db.py` directly; there is no repository layer or async database driver. Sessions commit on success, roll back on failure, and close their connections. Read-only sessions open SQLite in `mode=ro`; operations that must serialize state changes use `BEGIN IMMEDIATE`.

Existing table names, UTC ISO-text timestamps, JSON contents, and SQLite files are preserved. Initialization creates missing tables and immutable-entry triggers; it does not rewrite data or alter existing tables. No reset or export/import is needed for this ORM migration. Future column/constraint changes will need explicit schema migrations; `create_all` is not a schema-upgrade framework.

Tests include a pre-ORM database fixture, ORM and raw-SQL constraint checks, rollback after partial work, concurrent duplicate ticks, and enforced read-only connections.

## World clock (UTC)

World time follows real time 1:1, not a counter advanced by ticks. `GET /clock` returns the current timestamp with an explicit `UTC` label; API documentation also identifies the timezone. The future simulation loop will rest one real hour after each completed tick.

On restart, time since the last checkpoint is recorded in SQLite's `downtime` table:

- 22:00–06:00 UTC: `sleep` (22:00 inclusive, 06:00 exclusive).
- 06:00–22:00 UTC: `mundane`.
- Gaps crossing boundaries are split; no deliveries, encounters, or diary entries are invented.

These classifications apply only to downtime. Running the app at night does not prevent events.

A checkpoint is saved every 30 seconds and on graceful shutdown. After a crash, up to 30 seconds of live time may be classified as downtime. A backwards clock at startup is rejected rather than silently rewriting history. Run only one app instance against a given database; process supervision, not multiple workers, owns this clock until the simulation worker is introduced.

## Starting world

`GET /world` returns the courier, places, people, and delivery queue, with an explicit UTC label. It is read-only and never triggers generation.

The initial fixture is a depot, records office, and repair shop; Mara receives at the office and Ivo works at the shop. Two packages need carrying between them, with deadlines four and six real hours after the world is first created. Names and stops are starter content, not a generated city.

The world is seeded once, transactionally. Restarting preserves changes and does not reset deadlines, restore completed or removed deliveries, or invent activity during downtime. Tables use foreign keys and delivery-state constraints; the shared SQLAlchemy session setup enables foreign keys on every connection.

These people are a starting cast, not a fixed population limit. On a late handoff, inspector Ren arrives, records the time, and leaves. His encounter remains in history, but he does not appear as permanent staff on future visits. Changing staff and establishments are future event types, not autonomous city simulation. No diary entry is published by initialization.

## Simulation

`still.simulation.options(path)` exposes legal actions: travel toward a queued pickup or carried parcel's destination, collect one parcel, hand it to its recipient, or wait. `tick(path, tick_key, now, action)` validates the action against current state and atomically stores the tick, events, encounters, and resulting state. A repeated tick key returns its original result without applying anything twice; backwards tick times are rejected.

Every tick is stored, even a quiet wait. Deadlines are checked against actual UTC, and a missed deadline is recorded once when next observed, not fabricated as an encounter during downtime. A late recipient still accepts a parcel, with an inspector attending that handoff. Once the queue is finished, another parcel becomes available with a six-hour deadline. Regular encounters accumulate counts and timestamps.

The initial deterministic writing rule marks first meetings, newly observed missed deadlines, and inspections as candidates for publication. Routine collection, on-time handoff, repeat encounters, and waiting stay silent unless another qualifying event occurred. No score model or random writing gate is used. `still.engine.run_tick` connects a legal choice, simulation consequences, and publication; the hourly worker will drive it next.

## Model choices and publication

The model chooses a numbered action using a JSON schema containing only legal indices. Code validates the result again; invalid/unavailable choice output records a quiet wait and a failure, never an invented action. The model receives UTC time and the current hard facts, not ownership of state.

Qualifying ticks get one writing attempt based on their recorded event facts. Other ticks do not call the writer. Empty, truncated, overlong, non-first-person, reader-addressing, and obvious comparison/emotion output is rejected. These checks are deliberately small heuristics, not a complete semantic proof: factuality and voice still need ongoing sample review. The writer receives no hardware metrics or technical explanation of the premise.

Published entries live in `entries`; SQLite triggers prohibit updates and deletes. `publications` records silent, writing, published, or failed outcomes. Duplicate tick keys never trigger fresh inference. Failed or interrupted writing is not retried later; the sim's history still records what happened, while the diary may stay blank. No draft/rewrite workflow, hosted judge, score gate, or markdown export is used.

## Tests

```sh
uv run python -m unittest discover -s tests
```

## Local inference

Run llama.cpp's `llama-server` natively on macOS for Metal acceleration:

```sh
brew install llama.cpp
llama-server \
  --hf-repo ggml-org/gemma-4-E2B-it-GGUF \
  --hf-file gemma-4-E2B-it-Q4_0.gguf \
  --alias still \
  --ctx-size 4096 \
  --reasoning off \
  --host 127.0.0.1 \
  --port 8080
```

The first launch downloads weights into llama.cpp's cache outside the repo. Leave this running in a separate terminal. This is a text-only test; no image input is needed.

Once model integration is added, the container can reach the host server through `host.docker.internal`; it must listen on an interface reachable from Docker. Keep it restricted to trusted clients. Model requests must never originate from viewer requests.

Run the one-shot voice test:

```sh
uv run python -m still.prose --model still
```

Use `--base-url http://host.docker.internal:8080/v1` when running the command inside Docker. The test prints one entry without publishing or changing SQLite. It is not a substitute for testing prose on the Pi later.

SQLite will be the source of truth for published entries. No `diary.md` export is required.
