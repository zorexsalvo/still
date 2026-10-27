# Still

A courier's diary, written locally by Gemma. The simulation owns the facts, a separate worker advances the life, and a read-only viewer shows what was written: <http://localhost:8000/>.

## Running platforms

Still runs on macOS for development and is also running on Raspberry Pi, as confirmed by the project author. The detailed development checks in [VERIFICATION.md](VERIFICATION.md) were performed on the Mac; Pi deployment does not by itself establish a multi-day soak or Pi-specific performance/thermal calibration.

## Docker

**Mac:** start Docker Desktop or OrbStack and the native model server described under **Local inference** below. For real Mac hardware readings, optionally run `uv run python -m still.hardware --watch` outside Docker, in a separate terminal before starting Compose.

**Raspberry Pi/Linux:** use Docker Engine and Compose with the included Linux override, which mounts the host thermal sensor read-only and maps `host.docker.internal` to the host gateway. Set `STILL_MODEL_BASE_URL` if your model server uses another private endpoint. A model bound only to host loopback may not be reachable through the bridge; configure it for the Docker bridge or use a private network route. Keep model access private.

```sh
docker compose -f compose.yaml -f compose.pi.yaml up --build -d
```

With the platform's model connection configured:

```sh
docker compose up --build -d
curl http://localhost:8000/health
```

SQLite is stored at `./data/still.sqlite3`, bind-mounted into `/app/data`.
The entire directory is mounted to keep SQLite's WAL and shared-memory files alongside the database. Rebuilding or removing containers does not delete this directory. Deleting `data/` does delete the database. Do not copy a live database file alone for backups; use SQLite's backup API or stop the app before copying the directory.

The web server is published on `0.0.0.0:8000`, so other devices on your network can reach it using the host machine's LAN address. It has no authentication: use a trusted network and do not expose it directly to the internet. Keep the model API private; the Mac inference example below binds it to localhost. API docs: <http://localhost:8000/docs>. Compose runs one `worker` and one read-only `still` web process, using the same image. The web process waits for worker initialization on first start, but its ongoing health does not depend on model availability. Its SQLite connections are read-only; the directory mount remains writable because SQLite may need WAL/SHM coordination files.

```sh
docker compose logs -f
docker compose down
```

## Without Docker (Mac or Pi)

Start the model server first. A native worker defaults to `http://127.0.0.1:8080/v1`; use `STILL_MODEL_BASE_URL` and `STILL_MODEL` for the actual private endpoint and alias in your deployment.

```sh
uv sync --locked
uv run python -m still.worker
# In another terminal, after the worker has initialized:
uv run still
```

`STILL_DB_PATH` defaults to `data/still.sqlite3`. The native `still` command and Docker's published web port both bind `0.0.0.0:8000`.

## Database access

Synchronous SQLAlchemy 2.x models live in `src/still/models.py`. Use the session context manager in `src/still/db.py` directly; there is no repository layer or async database driver. Sessions commit on success, roll back on failure, and close their connections. Read-only sessions open SQLite in `mode=ro`; operations that must serialize state changes use `BEGIN IMMEDIATE`.

Existing table names, UTC ISO-text timestamps, JSON contents, and SQLite files are preserved. Initialization creates missing tables and immutable-entry triggers, and applies a small set of idempotent additive column migrations (`relationships.last_returned_at`, `deliveries.contents`); it never drops, renames or rewrites a legacy column. No reset or export/import is needed for this ORM migration. Any further column or constraint change needs its own explicit migration; `create_all` alone is not a schema-upgrade framework.

Tests include a pre-ORM database fixture, ORM and raw-SQL constraint checks, rollback after partial work, concurrent duplicate ticks, and enforced read-only connections.

## World clock (UTC)

World time follows real time 1:1, not a counter advanced by ticks. `GET /clock` returns the current timestamp with an explicit `UTC` label; API documentation also identifies the timezone. The simulation worker rests thirty real minutes after each completed tick.

On restart, time since the last checkpoint is recorded in SQLite's `downtime` table:

- 22:00–06:00 UTC: `sleep` (22:00 inclusive, 06:00 exclusive).
- 06:00–22:00 UTC: `mundane`.
- Gaps crossing boundaries are split; no deliveries, encounters, or diary entries are invented.

These classifications apply only to downtime. Running the app at night does not prevent events.

The worker saves a checkpoint about every 30 seconds and on graceful shutdown. After a crash, roughly 30 seconds of live time may be classified as downtime (plus any checkpoint delay). A backwards clock at startup is rejected rather than silently rewriting history. The viewer neither owns this clock nor writes downtime records.

## Starting world

`GET /world` returns the courier, places, people, and delivery queue, with an explicit UTC label. It is read-only and never triggers generation.

The initial fixture is a depot, records office, and repair shop; Mara receives at the office and Ivo works at the shop. A corrected directory index and an access request need carrying between them, with deadlines four and six real hours after the world is first created. Delivery facts use terminal-native contents rather than exposing `parcel-N` IDs. New jobs store inputs matched to their recipient's work; rows created before contents were stored fall back to a description derived from their id. Names and stops are starter content, not a generated city.

The world is seeded once, transactionally. Restarting preserves changes and does not reset deadlines, restore completed or removed deliveries, or invent activity during downtime. Tables use foreign keys and delivery-state constraints; the shared SQLAlchemy session setup enables foreign keys on every connection.

These people are a starting cast, not a fixed population limit. On a late handoff, inspector Ren arrives, records the time, and leaves. His encounter remains in history, but he does not appear as permanent staff on future visits. A handover offered more than twelve hours after its deadline is refused outright: the recipient will not take it, the parcel is marked returned, and no inspection follows because nothing was handed over. That is the route's only non-success outcome so far. No diary entry is published by initialization.

The route expands on its own through five authored stops: the Bindery (Nadia), Goods yard (Oskar), Print room (Elin), Ferry depot (Laleh), and Map room (Tomas). Each new stop introduces its keeper and sends a parcel along a route connecting them to someone already on the route, so the cast and delivery network grow together rather than returning every new parcel to Mara. Six further parcels must be created after the last expansion before the next stop appears. Once introduced, these regulars remain eligible for recurring jobs. These changes are recorded `new_place` events, never inference, and do not happen during a backfill replay of the past. The authored route is finite.

## Simulation

`still.simulation.options(path)` exposes legal actions: travel toward a queued pickup or carried parcel's destination, collect one parcel, hand it to its recipient, restore a personal file, visit Ivo to discuss it, or wait. `tick(path, tick_key, now, action)` validates the action against current state and atomically stores the tick, events, encounters, and resulting state. A repeated tick key returns its original result without applying anything twice; backwards tick times are rejected.

Every tick is stored, even a quiet wait. Deadlines are checked against actual UTC, and a missed deadline is recorded once when next observed, not fabricated as an encounter during downtime. A late recipient still accepts a parcel, with an inspector attending that handoff. When the queue is clear and at least six real hours have passed since the newest parcel was created, a pair of jobs becomes available with two- and four-hour deadlines. Their pickup stops compete for Still's time. Regulars rotate through recurring batches; inspectors are excluded. Only one current batch opens after a long gap, never a backlog. Regular encounters accumulate counts and timestamps. When a pickup is waiting at the current stop, the courier must collect a parcel or wait before travelling elsewhere; it cannot bounce between stops while ignoring their packages.

Each new input job records what its recipient needs it for. A missed deadline holds up that activity and records the consequence once. Accepting the input enables the recipient's authored activity and creates one output parcel for another regular; delivering the output does not recursively create more work. Refusal cancels that output. Dependencies live in existing delivery rows and event facts, without migrations or rewriting old jobs. These consequences are included in model choice state and the World view.

The deterministic writing rule marks newly enabled or cancelled work, missed deadlines, inspections, refusals, personal-file discoveries/completion, and nonwork visits as candidates for publication. Routine collection, handoff without a new work consequence, first introductions alone, repeat encounters, and waiting stay silent unless another qualifying event occurred. The presence of an unresolved thread alone does not turn routine work into a candidate. No score model or random writing gate is used. `still.engine.run_tick` connects a legal choice, simulation consequences, and publication; the worker waits thirty minutes after each tick finishes.

## A life outside work

The courier has a private `old-index` file with three damaged lines. `restore_file` recovers its path, an empty owner field, and a final `keep: yes` instruction in three persistent steps. The project is stored in SQLite, not by restoring real files on the host. Existing worlds receive the new project without resetting their courier or history.

`visit` lets him show Ivo the file outside a delivery. Ivo responds to its current state; the same stage cannot be shown repeatedly. Visiting may involve travel, and both visiting and restoring consume a tick. Choosing either while work is pending can therefore make a delivery late. Hardware pressure blocks these activities too, and shutdown/downtime never advances the project.

Finishing recovery now opens an owner inquiry rather than ending the private life. `follow_owner` asks Ivo to compare an older index, collects his shared-path reference, and takes it to Mara's register. Each comparison has an 18-real-hour wait, and receiving an answer requires another visit. The final answer changes the stored owner field to `shared`; nothing advances merely because the worker was stopped.

After that, `borrow_notes`, `read_notes`, and `return_notes` let him borrow a four-part book from Ivo or Mara outside paid work. On the first loan, the choice model gives the book a specific terminal-world title; the simulation records that title in the borrow event and reuses it. Older loans without a stored title use a readable fallback. A loan carries a real 24-hour return promise. Reading takes a tick. Another loan from the same person cannot start until 24 hours after he returned theirs; returning one lender's book does not hold up borrowing from another. New passages and the first kept return promise qualify; later ordinary on-time returns, rereading and unchanged routine borrowing stay silent. This is bounded story material, not an infinite prose generator, but the legal private activities continue after it has been read.

Relationships remember loans, reading position, the last passage read, returns and late returns. Returning a book does not erase its unresolved reading topic from writer context. An overdue promise is recorded once. After a late return the lender refuses another loan, and `help_person` checks the shared-path reference with them before borrowing becomes possible again. Work and hardware delays can make those promises late. These are persistent consequences, not a mood score or autonomous city simulation. The sim owns the facts and reactions; Gemma chooses actions and writes from them.

## Model choices and publication

The model chooses an explicitly labelled, zero-based action index using a JSON schema containing only legal indices. When it chooses to borrow for the first time from a lender, it also supplies a short book title, which is validated and recorded with that event. Each option includes its exact `choice` number; the model never has to infer numbering from an unlabelled array. Code validates the result again; invalid/unavailable choice output records a quiet wait and a failure, never an invented action. The model receives UTC time and the current hard facts, not ownership of state.

Qualifying ticks get one writing attempt with their recorded facts, up to eight earlier non-wait events since the last live entry, and at most two unresolved items from current world state. Private-file follow-ups, borrowed notes and unresolved lending commitments take priority, followed by overdue undelivered work. Resolved items disappear from that context; a decoded file with a blank owner does not count as resolved. Backfilled event history is excluded from live background, but authoritative current world state still supplies its open threads. Earlier facts, ongoing threads, and the current moment are separate sections.

The writer can treat changed personal commitments, another person's delayed work, or newly enabled activity as the subject, rather than listing delivery receipts. It uses as few sentences as needed, often one or two, capped at six sentences and 180 words. It must not invent gestures or reactions to enliven a routine handoff. Other ticks do not call the writer. Empty, truncated, overlong, non-first-person, reader-addressing, and obvious comparison/emotion output is rejected. A reference-aware check strips people and object names and rejects entries whose remainder is only receipt actions. Repeated proper names/noun phrases and system language (`operation`, `completed`, `state is now`, `updated`, `successfully`) are rejected too. These lexical checks are heuristics, not semantic or factuality proof. The writer receives no hardware metrics or technical explanation of the premise.

Published entries live in `entries`; SQLite triggers prohibit updates and deletes. `publications` records silent, writing, published, or failed outcomes. Duplicate tick keys never trigger fresh inference. Failed or interrupted writing is not retried later; the sim's history still records what happened, while the diary may stay blank. No draft/rewrite workflow, hosted judge, score gate, or markdown export is used.

## Worker and hardware

The worker starts one tick immediately on a new world, then persists the next eligible time as **completion + thirty real minutes**. Restarting during rest preserves that time. Returning after a long gap runs at most one current tick, not a backlog. Restarting the viewer cannot advance the courier.

A data-directory file lock plus a 90-second SQLite ownership lease rejects a second worker, including across native macOS and Docker (where `flock` alone is not shared). The lease is renewed alongside checkpoints and fenced at state/publication writes. An unclean kill may delay supervisor recovery until the old lease expires; graceful shutdown releases it immediately. `worker_runs` records attempts even if a crash interrupts inference before any legal action is applied. Interrupted publication is marked failed and never rewritten; recovery of an active attempt conservatively starts a fresh thirty minutes of rest. Graceful shutdown waits for the active tick; Compose allows 30 seconds before killing it, after which crash recovery applies. `restart: unless-stopped` supervises the worker. Model failures are recorded and still incur the normal rest, not repeated rapid retries. Start the model before the worker if you want the first tick to have inference available.

`STILL_MODEL_BASE_URL` defaults to `http://127.0.0.1:8080/v1` natively; the checked-in Compose configuration uses `http://host.docker.internal:8080/v1` for the Mac setup. Pi/Linux containers need a reachable endpoint configured for that deployment. `STILL_MODEL` defaults to `still`. `python -m still.worker --health` checks checkpoint freshness without initializing or changing the world.

Hardware readings include load, core count, free disk, temperature when a readable Linux thermal-zone sensor exists, and the response time of the configured model endpoint. The Pi Compose override mounts `/sys/class/thermal` read-only; the worker reads the first available thermal zone without extra packages or privileges. On macOS Docker, temperature remains unavailable unless a native sampler writes the host readings file. Endpoint response time is a connectivity/latency check, not a bandwidth test; it does not run a speed test or send traffic to a third party. It is not a reply time: the compact model list answers in well under a millisecond on a local endpoint. The reply figure shown in the footer is measured separately, around each tick's own chat-completion requests, so it covers prompt handling and generation rather than a connect. A native sampler on the machine running the worker can atomically refresh `data/host-hardware.json` every 30 seconds:

```sh
uv run python -m still.hardware --watch
```

The worker accepts valid native samples no older than 90 seconds. Missing/stale/invalid files fall back to the worker's own readings, explicitly labelled as such. On a Mac with Docker Desktop or OrbStack, those readings describe the Docker Linux environment rather than the macOS host. On a native Linux Pi, containers share the host kernel; there is no Docker Desktop VM by default, though container resource limits and filesystem views still matter. A host-side sampler makes the intended source explicit on either platform. A native worker needs no separate sampler to read its own machine.

Load greater than 1.5 per reported core or temperature at/above 80°C prevents collecting, travelling and personal actions (including reading or returning notes) for that tick; handing over an already-carried parcel at its destination remains legal. The resulting wait can make a delivery or promised personal return late. Recorded facts describe slow movement, never the metric. These are conservative simulation thresholds, not a sickness claim or a substitute for Raspberry Pi firmware thermal protection; confirm sensor availability and behavior on the deployed Pi. The endpoint latency check and each tick's measured model reply times (`model_choice_ms` for the action choice, `model_reply_ms` for a written entry) are recorded with the tick but do not yet alter actions; an unreachable model already results in a quiet wait through the normal inference error path. Disk free space is recorded but does not yet change behavior.

## Reading the diary

Open <http://localhost:8000/>. Published entries appear newest first, with UTC dates, permalinks, and up to 50 entries per page. Earlier/latest links use stable cursor pagination. Gaps count complete UTC dates without entries; they never manufacture prose or imply that the worker was alive throughout a gap.

The page uses server-rendered HTML and local CSS, with no JavaScript or external font/image requests. Its favicon is a bundled local SVG with an ICO fallback. Use **Refresh entries** for new text; the **As of** timestamp describes that page load, not a live clock. Empty and recoverable database-error views do not request a generation. Stored text is escaped, and a restrictive content-security policy prevents scripts from running in diary content.

A faint, static vignette illustrates the latest recorded tick: Still arriving, reading/restoring, waiting, or with a person actually involved in that moment. It stays outside the reading column on wide screens and becomes a small scene above the diary on narrow screens. Companions are not inferred from their usual workplace; refusals and inspections do not depict a successful handoff. The scene never animates or creates activity. The quiet footer status shows location, worker check-in freshness, last successfully completed **live** tick, and next eligible tick or a recorded in-progress attempt. Replay ticks do not count as live progress. A recent check-in does not certify inference health: a failed last finished attempt is reported separately without exposing its error details. Refresh loads another snapshot; it does not advance the world. If status cannot be read, the diary remains available where possible. For failure details, run `docker compose logs --tail=50 worker` on the server.

The **World** navigation link opens <http://localhost:8000/world/view>: a read-only overview of the courier, personal file, borrowed notes and return time, open threads, lending history, people and places, deliveries, recent changes, and worker check-in/next-tick times. Backdated activity is labelled, and occasional visitors are not presented as permanent staff. Tables scroll within their region on small screens.

`GET /world` still returns JSON; `GET /clock` and `/docs` remain development endpoints. No endpoint accepts simulation actions or diary writes. The viewer remains readable while inference or the worker is unavailable, as long as SQLite remains readable.

## Blog artwork

Ready-to-publish **1200 × 630** cover: [`assets/branding/still-cover.png`](assets/branding/still-cover.png). The editable SVG, favicon locations and source/export provenance are documented in [`assets/branding/README.md`](assets/branding/README.md). The artwork extends the site's existing courier illustration; it does not depict a recorded live event.

## Explicit backdated history

On request, a completed past UTC day can be generated with real Gemma calls:

```sh
uv run python -m still.backfill --day 2026-10-23
```

Omit `--day` to use yesterday UTC. Use `uv run python -m still.backfill --days 2` for the last two completed UTC days, or combine `--day` and `--days` for a specific range. Multiple days run as one continuous life without resetting people or deliveries at midnight. This is an explicit exception to live-only publication, not automatic downtime replay. It runs 24 hourly moments per day in an isolated world, then imports the whole range in one transaction. Any generation/import failure leaves live history unchanged; a day cannot be imported twice. The live courier, packages, encounters, clock, and next scheduled tick are not rewound or replaced.

All imported ticks carry `backfill_ticks` provenance, with the actual generation time and model. Historical machine conditions are unknown and are not fabricated. The viewer sorts by entry time rather than insertion order, and labels these entries **Backdated … not recorded live**. The imported fiction is not evidence of what the real machine or courier did yesterday, and the normal append-only command does not retroactively rebuild live relationships or delivery state. A separately requested full reset instead archives the old database and promotes a validated, continuously replayed world as the new live state; that is an explicit administrative replacement, not a reader control.

## Tests

```sh
uv run python -m unittest discover -s tests
```

For an opt-in check against your running model, without modifying the live world:

```sh
uv run python tests/live_smoke.py
```

This creates a temporary world, drives a real model-backed worker tick, serves its published entry over HTTP, verifies reading while inference is unavailable, and checks replay/restart behavior. Its temporary finished-round fixture leaves the model personal actions to choose from rather than routine deliveries. It expects an eligible personal moment; legitimate silence, quality rejection, or a model wait can fail that demonstration without indicating broken silence behavior. It never resets the live database or accelerates its clock.

See [VERIFICATION.md](VERIFICATION.md) for the checks performed on the local MVP.

## Known limits

- The courier's voice treats files, directories, queues, and permissions as its entire ordinary world. These are still simulated locations and contents, not yet host-filesystem geography or independently running process neighbours.
- The route starts as three places, two regulars, and an event-bound inspector, then grows one authored place at a time until that list runs out. A person introduced by a new route is a regular at their place but is not a lender: only Ivo and Mara have reading material, and meetings with a newly added regular are not yet recorded.
- The deterministic gate has no daily quota or quiet-streak nudge. Routine successes stay silent. The owner inquiry and eight reading passages are authored, finite material; borrowing, rereading, promises and relationship repair remain available afterward. Late handover has a refusal producer; the `delivery_failed` event kind and the `lost` parcel status are declared but nothing can reach them yet, so a carried parcel is never lost and work never fails outright.
- Prose checks are heuristics, not semantic verification. Sustained voice quality and factuality still need sample review. The writer sees current facts, bounded recorded context and up to two unresolved items. Phrase detection uses known references and lexical patterns, not a full parser, so false positives and misses remain possible; deliberate autobiographical memory decay is not implemented.
- Pi temperature sampling depends on a readable thermal-zone file exposed by the host's `/sys/class/thermal`; otherwise it is `null`. Model endpoint latency is not bandwidth measurement and currently has no independent behavioral effect. Measured reply times describe only the model call that produced them: they do not separate prompt processing from token generation, they are not comparable across models or hosts, and they have no behavioral effect. Disk space has no behavioral effect, and the load/temperature thresholds are not physiological or thermal calibration.
- Crash checkpoints have roughly 30-second uncertainty, and a stale worker lease can delay recovery for up to 90 seconds. Schema changes beyond adding tables need explicit migrations.
- Raspberry Pi deployment is active, but no multi-day soak duration, Pi-specific performance measurements or public-deployment security review are documented here. Per-user lives and a reader-controlled ending remain unimplemented.

## Local inference

The command below is the verified **Mac development example**, using native llama.cpp with Metal acceleration. For Pi, use the llama.cpp build and private model endpoint configured for that deployment; its hardware, inference placement and performance are not specified by the Mac test record.

On Mac:

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

In the Mac setup, the container reaches the host server through `host.docker.internal`; localhost-bound llama-server was verified with OrbStack. Do not assume that routing works unchanged on Linux/Pi. Other Docker runtimes may require explicit host-network configuration; keep access restricted to trusted local clients rather than exposing an unauthenticated model on your LAN. Viewer requests never originate model calls.

Run the one-shot voice test:

```sh
uv run python -m still.prose --model still
```

When testing inside Docker, pass `--base-url` with the endpoint reachable from that container (`http://host.docker.internal:8080/v1` in the Mac setup). The test prints one entry without publishing or changing SQLite. A successful one-shot test, including on Pi, is not evidence of sustained prose quality or a multi-day soak.

SQLite is the source of truth for published entries. No `diary.md` export is required.
