# Still

A courier's diary, written by a local model. Initial scaffold: FastAPI and persistent SQLite; simulation and model integration are not implemented yet.

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

## World clock (UTC)

World time follows real time 1:1, not a counter advanced by ticks. `GET /clock` returns the current timestamp with an explicit `UTC` label; API documentation also identifies the timezone. The future simulation loop will rest one real hour after each completed tick.

On restart, time since the last checkpoint is recorded in SQLite's `downtime` table:

- 22:00–06:00 UTC: `sleep` (22:00 inclusive, 06:00 exclusive).
- 06:00–22:00 UTC: `mundane`.
- Gaps crossing boundaries are split; no deliveries, encounters, or diary entries are invented.

These classifications apply only to downtime. Running the app at night does not prevent events.

A checkpoint is saved every 30 seconds and on graceful shutdown. After a crash, up to 30 seconds of live time may be classified as downtime. A backwards clock at startup is rejected rather than silently rewriting history. Run only one app instance against a given database; process supervision, not multiple workers, owns this clock until the simulation worker is introduced.

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
