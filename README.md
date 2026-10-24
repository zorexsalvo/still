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

## Tests

```sh
uv run python -m unittest discover -s tests
```

## Local inference

Run llama.cpp's `llama-server` natively on macOS for Metal acceleration. Once model integration is added, the container can reach the host server through `host.docker.internal`; it must listen on an interface reachable from Docker. Keep it restricted to trusted clients. Model requests must never originate from viewer requests.

Run the one-shot voice test with the model ID reported by `GET /v1/models`:

```sh
uv run python -m still.prose --model YOUR_MODEL_ID
```

Use `--base-url http://host.docker.internal:8080/v1` when running the command inside Docker. The test prints one entry without publishing or changing SQLite. It is not a substitute for testing prose on the Pi later.

SQLite will be the source of truth for published entries. No `diary.md` export is required.
