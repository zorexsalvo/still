# Still

A courier's diary, written locally by Gemma. The simulation owns the facts, a separate worker advances the life, and a read-only viewer shows what was written: <http://localhost:8000/>.

## Docker (Mac first)

Start Docker Desktop or OrbStack and the native model server described under **Local inference** below. For real Mac hardware readings, optionally run `uv run python -m still.hardware --watch` in a separate terminal before starting Compose. Then:

```sh
docker compose up --build -d
curl http://localhost:8000/health
```

SQLite is stored at `./data/still.sqlite3`, bind-mounted into `/app/data`.
The entire directory is mounted to keep SQLite's WAL and shared-memory files alongside the database. Rebuilding or removing containers does not delete this directory. Deleting `data/` does delete the database. Do not copy a live database file alone for backups; use SQLite's backup API or stop the app before copying the directory.

The server is exposed only on localhost. API docs: <http://localhost:8000/docs>. Compose runs one `worker` and one read-only `still` web process, using the same image. The web process waits for worker initialization on first start, but its ongoing health does not depend on model availability. Its SQLite connections are read-only; the directory mount remains writable because SQLite may need WAL/SHM coordination files.

```sh
docker compose logs -f
docker compose down
```

## Without Docker

```sh
uv sync --locked
uv run python -m still.worker
# In another terminal, after the worker has initialized:
uv run still
```

`STILL_DB_PATH` defaults to `data/still.sqlite3`. The native `still` command binds only localhost. Docker's web command listens inside its container, with Compose publishing it only on `127.0.0.1`.

## Database access

Synchronous SQLAlchemy 2.x models live in `src/still/models.py`. Use the session context manager in `src/still/db.py` directly; there is no repository layer or async database driver. Sessions commit on success, roll back on failure, and close their connections. Read-only sessions open SQLite in `mode=ro`; operations that must serialize state changes use `BEGIN IMMEDIATE`.

Existing table names, UTC ISO-text timestamps, JSON contents, and SQLite files are preserved. Initialization creates missing tables and immutable-entry triggers; it does not rewrite data or alter existing tables. No reset or export/import is needed for this ORM migration. Future column/constraint changes will need explicit schema migrations; `create_all` is not a schema-upgrade framework.

Tests include a pre-ORM database fixture, ORM and raw-SQL constraint checks, rollback after partial work, concurrent duplicate ticks, and enforced read-only connections.

## World clock (UTC)

World time follows real time 1:1, not a counter advanced by ticks. `GET /clock` returns the current timestamp with an explicit `UTC` label; API documentation also identifies the timezone. The simulation worker rests one real hour after each completed tick.

On restart, time since the last checkpoint is recorded in SQLite's `downtime` table:

- 22:00–06:00 UTC: `sleep` (22:00 inclusive, 06:00 exclusive).
- 06:00–22:00 UTC: `mundane`.
- Gaps crossing boundaries are split; no deliveries, encounters, or diary entries are invented.

These classifications apply only to downtime. Running the app at night does not prevent events.

The worker saves a checkpoint about every 30 seconds and on graceful shutdown. After a crash, roughly 30 seconds of live time may be classified as downtime (plus any checkpoint delay). A backwards clock at startup is rejected rather than silently rewriting history. The viewer neither owns this clock nor writes downtime records.

## Starting world

`GET /world` returns the courier, places, people, and delivery queue, with an explicit UTC label. It is read-only and never triggers generation.

The initial fixture is a depot, records office, and repair shop; Mara receives at the office and Ivo works at the shop. A corrected directory index and an access request need carrying between them, with deadlines four and six real hours after the world is first created. Delivery facts use terminal-native contents rather than exposing `parcel-N` IDs. For now, descriptions are derived from legacy IDs (or use an already-named ID); richer contents will need explicit stored metadata. Names and stops are starter content, not a generated city.

The world is seeded once, transactionally. Restarting preserves changes and does not reset deadlines, restore completed or removed deliveries, or invent activity during downtime. Tables use foreign keys and delivery-state constraints; the shared SQLAlchemy session setup enables foreign keys on every connection.

These people are a starting cast, not a fixed population limit. On a late handoff, inspector Ren arrives, records the time, and leaves. His encounter remains in history, but he does not appear as permanent staff on future visits. Changing staff and establishments are future event types, not autonomous city simulation. No diary entry is published by initialization.

## Simulation

`still.simulation.options(path)` exposes legal actions: travel toward a queued pickup or carried parcel's destination, collect one parcel, hand it to its recipient, or wait. `tick(path, tick_key, now, action)` validates the action against current state and atomically stores the tick, events, encounters, and resulting state. A repeated tick key returns its original result without applying anything twice; backwards tick times are rejected.

Every tick is stored, even a quiet wait. Deadlines are checked against actual UTC, and a missed deadline is recorded once when next observed, not fabricated as an encounter during downtime. A late recipient still accepts a parcel, with an inspector attending that handoff. Once the queue is finished, another parcel becomes available with a six-hour deadline. Regular encounters accumulate counts and timestamps. When a pickup is waiting at the current stop, the courier must collect a parcel or wait before travelling elsewhere; it cannot bounce between stops while ignoring their packages.

The initial deterministic writing rule marks first meetings, newly observed missed deadlines, and inspections as candidates for publication. Routine collection, on-time handoff, repeat encounters, and waiting stay silent unless another qualifying event occurred. No score model or random writing gate is used. `still.engine.run_tick` connects a legal choice, simulation consequences, and publication; the hourly worker drives it.

## Model choices and publication

The model chooses an explicitly labelled, zero-based action index using a JSON schema containing only legal indices. Each option includes its exact `choice` number; the model never has to infer numbering from an unlabelled array. Code validates the result again; invalid/unavailable choice output records a quiet wait and a failure, never an invented action. The model receives UTC time and the current hard facts, not ownership of state.

Qualifying ticks get one writing attempt based on their recorded event facts, plus up to eight earlier events since the last live entry. Backfilled history is excluded from live context, and earlier facts are clearly separated from the current moment. The writer aims for three to five short sentences when supported, with a cap of six sentences and 180 words; genuinely small moments may stay shorter. It selects details rather than padding or enumerating every action. Other ticks do not call the writer. Empty, truncated, overlong, non-first-person, reader-addressing, and obvious comparison/emotion output is rejected. These checks are deliberately small heuristics, not a complete semantic proof: factuality and voice still need ongoing sample review. The writer receives no hardware metrics or technical explanation of the premise.

Published entries live in `entries`; SQLite triggers prohibit updates and deletes. `publications` records silent, writing, published, or failed outcomes. Duplicate tick keys never trigger fresh inference. Failed or interrupted writing is not retried later; the sim's history still records what happened, while the diary may stay blank. No draft/rewrite workflow, hosted judge, score gate, or markdown export is used.

## Worker and hardware

The worker starts one tick immediately on a new world, then persists the next eligible time as **completion + one real hour**. Restarting during rest preserves that time. Returning after a long gap runs at most one current tick, not a backlog. Restarting the viewer cannot advance the courier.

A data-directory file lock plus a 90-second SQLite ownership lease rejects a second worker, including across native macOS and Docker (where `flock` alone is not shared). The lease is renewed alongside checkpoints and fenced at state/publication writes. An unclean kill may delay supervisor recovery until the old lease expires; graceful shutdown releases it immediately. `worker_runs` records attempts even if a crash interrupts inference before any legal action is applied. Interrupted publication is marked failed and never rewritten; recovery of an active attempt conservatively starts a fresh hour of rest. Graceful shutdown waits for the active tick; Compose allows 30 seconds before killing it, after which crash recovery applies. `restart: unless-stopped` supervises the worker. Model failures are recorded and still incur the normal rest, not repeated rapid retries. Start the model before the worker if you want the first tick to have inference available.

`STILL_MODEL_BASE_URL` defaults to `http://127.0.0.1:8080/v1` natively; Compose uses `http://host.docker.internal:8080/v1`. `STILL_MODEL` defaults to `still`. `python -m still.worker --health` checks checkpoint freshness without initializing or changing the world.

Portable readings include load, core count, free disk, platform, and sample time. CPU temperature is explicitly unavailable on macOS; no privileged sensor tools or invented values are used. A native sampler can atomically refresh `data/host-hardware.json` every 30 seconds:

```sh
uv run python -m still.hardware --watch
```

The worker accepts valid native samples no older than 90 seconds. Missing/stale/invalid files fall back to the worker's own readings, explicitly labelled as such: inside Docker those describe the VM, not the Mac. A native worker needs no separate sampler to read its own machine.

Load greater than 1.5 per reported core prevents collecting or travelling for that tick; handing over an already-carried parcel at its destination remains legal. The resulting wait can make a delivery late. Recorded facts describe slow movement, never the metric. This threshold is a simple sim rule, not calibrated illness or thermal behavior; real Pi thermal calibration is deferred. Disk free space is recorded but does not yet change behavior.

## Reading the diary

Open <http://localhost:8000/>. Published entries appear newest first, with UTC dates, permalinks, and up to 50 entries per page. Earlier/latest links use stable cursor pagination. Gaps count complete UTC dates without entries; they never manufacture prose or imply that the worker was alive throughout a gap.

The page uses server-rendered HTML and local CSS, with no JavaScript, external fonts, or image requests. Use **Refresh entries** for new text; the **As of** timestamp describes that page load, not a live clock. Empty and recoverable database-error views do not request a generation. Stored text is escaped, and a restrictive content-security policy prevents scripts from running in diary content.

`GET /clock`, `GET /world`, and `/docs` remain local development endpoints. No endpoint accepts simulation actions or diary writes. The viewer remains readable while inference or the worker is unavailable, as long as SQLite remains readable.

## Explicit backdated history

On request, a completed past UTC day can be generated with real Gemma calls:

```sh
uv run python -m still.backfill --day 2026-10-23
```

Omit `--day` to use yesterday UTC. Use `uv run python -m still.backfill --days 2` for the last two completed UTC days, or combine `--day` and `--days` for a specific range. Multiple days run as one continuous life without resetting people or deliveries at midnight. This is an explicit exception to live-only publication, not automatic downtime replay. It runs 24 hourly moments per day in an isolated world, then imports the whole range in one transaction. Any generation/import failure leaves live history unchanged; a day cannot be imported twice. The live courier, packages, encounters, clock, and next scheduled tick are not rewound or replaced.

All imported ticks carry `backfill_ticks` provenance, with the actual generation time and model. Historical machine conditions are unknown and are not fabricated. The viewer sorts by entry time rather than insertion order, and labels these entries **Backdated … not recorded live**. The imported fiction is not evidence of what the real machine or courier did yesterday, and is not used to retroactively rebuild live relationships or delivery state.

## Tests

```sh
uv run python -m unittest discover -s tests
```

For an opt-in check against your running model, without modifying the live world:

```sh
uv run python tests/live_smoke.py
```

This creates a temporary world, drives a real model-backed worker tick, serves its published entry over HTTP, verifies reading while inference is unavailable, and checks replay/restart behavior. It expects the first tick to publish a first meeting; a legitimate silent tick under high load or a model wait can fail that demonstration without indicating broken silence behavior. It never resets the live database or accelerates its clock.

See [VERIFICATION.md](VERIFICATION.md) for the checks performed on the local MVP.

## Known limits

- The courier's voice treats files, directories, queues, and permissions as its entire ordinary world. These are still simulated locations and contents, not yet host-filesystem geography or independently running process neighbours.
- The route is a starter world: three places, two regulars, and an event-bound inspector. More encounter and establishment-change types are not implemented.
- The deterministic gate has no quiet-streak nudge. Routine successful work after initial meetings can stay unpublished indefinitely.
- Prose checks are heuristics, not semantic verification. Sustained voice quality and factuality still need sample review. The writer sees current-tick facts and bounded recent recorded context; deliberate autobiographical memory decay is not implemented.
- Native host sampling is optional. Without fresh host samples, Docker readings describe its VM; temperature is unavailable, and the load threshold is not Pi thermal calibration.
- Crash checkpoints have roughly 30-second uncertainty, and a stale worker lease can delay recovery for up to 90 seconds. Schema changes beyond adding tables need explicit migrations.
- No multi-day soak, Raspberry Pi deployment, public hosting, replay, doodles, per-user lives, or reader-controlled ending has been tested or added.

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

The container reaches the host server through `host.docker.internal`; localhost-bound llama-server was verified with OrbStack. Other Docker runtimes may require host-network configuration; keep access restricted to trusted local clients rather than exposing an unauthenticated model on your LAN. Viewer requests never originate model calls.

Run the one-shot voice test:

```sh
uv run python -m still.prose --model still
```

Use `--base-url http://host.docker.internal:8080/v1` when running the command inside Docker. The test prints one entry without publishing or changing SQLite. It is not a substitute for testing prose on the Pi later.

SQLite will be the source of truth for published entries. No `diary.md` export is required.
