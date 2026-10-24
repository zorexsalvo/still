-- Schema and sample history from the pre-ORM application (TEXT timestamps/JSON).
PRAGMA foreign_keys=ON;
CREATE TABLE places (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('depot', 'office', 'establishment')));
CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, place_id TEXT NOT NULL REFERENCES places(id));
CREATE TABLE courier (id INTEGER PRIMARY KEY CHECK (id = 1), place_id TEXT NOT NULL REFERENCES places(id), started_at TEXT NOT NULL);
CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    sender_id TEXT NOT NULL REFERENCES people(id), recipient_id TEXT NOT NULL REFERENCES people(id),
    origin_id TEXT NOT NULL REFERENCES places(id), destination_id TEXT NOT NULL REFERENCES places(id),
    created_at TEXT NOT NULL, due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'carried', 'delivered', 'returned', 'lost')),
    CHECK (origin_id != destination_id), CHECK (due_at > created_at)
);
CREATE TABLE world_clock (id INTEGER PRIMARY KEY CHECK (id = 1), last_seen TEXT NOT NULL);
CREATE TABLE downtime (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('sleep', 'mundane')));
CREATE TABLE ticks (id INTEGER PRIMARY KEY, tick_key TEXT NOT NULL UNIQUE, at TEXT NOT NULL, action TEXT NOT NULL, hardware TEXT NOT NULL, publish INTEGER NOT NULL DEFAULT 0 CHECK (publish IN (0, 1)));
CREATE TABLE events (id INTEGER PRIMARY KEY, tick_id INTEGER NOT NULL REFERENCES ticks(id), kind TEXT NOT NULL, facts TEXT NOT NULL, event_key TEXT UNIQUE);
CREATE TABLE encounters (person_id TEXT PRIMARY KEY REFERENCES people(id), first_at TEXT NOT NULL, last_at TEXT NOT NULL, count INTEGER NOT NULL CHECK (count > 0));
CREATE TABLE publications (tick_id INTEGER PRIMARY KEY REFERENCES ticks(id), status TEXT NOT NULL CHECK (status IN ('silent', 'writing', 'published', 'failed')), error TEXT);
CREATE TABLE entries (id INTEGER PRIMARY KEY, tick_id INTEGER NOT NULL UNIQUE REFERENCES ticks(id), at TEXT NOT NULL, text TEXT NOT NULL CHECK (length(trim(text)) > 0));
CREATE TABLE arrivals (tick_id INTEGER NOT NULL REFERENCES ticks(id), person_id TEXT NOT NULL REFERENCES people(id), PRIMARY KEY (tick_id, person_id));
CREATE TRIGGER entries_no_update BEFORE UPDATE ON entries BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END;
CREATE TRIGGER entries_no_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT, 'published entries are immutable'); END;

INSERT INTO places VALUES ('depot', 'Depot', 'depot'), ('records-office', 'Records office', 'office'), ('repair-shop', 'Repair shop', 'establishment');
INSERT INTO people VALUES ('mara', 'Mara', 'receptionist', 'records-office'), ('ivo', 'Ivo', 'repairer', 'repair-shop');
INSERT INTO courier VALUES (1, 'repair-shop', '2026-10-24T00:00:00.000000+00:00');
INSERT INTO deliveries VALUES
    ('parcel-1', 'ivo', 'mara', 'repair-shop', 'records-office', '2026-10-24T00:00:00.000000+00:00', '2026-10-24T04:00:00.000000+00:00', 'delivered'),
    ('parcel-2', 'mara', 'ivo', 'records-office', 'repair-shop', '2026-10-24T00:00:00.000000+00:00', '2026-10-24T06:00:00.000000+00:00', 'carried');
INSERT INTO world_clock VALUES (1, '2026-10-24T10:00:00.000000+00:00');
INSERT INTO downtime VALUES (1, '2026-10-24T02:00:00.000000+00:00', '2026-10-24T06:00:00.000000+00:00', 'sleep');
INSERT INTO ticks VALUES (1, 'legacy-tick', '2026-10-24T01:00:00.000000+00:00', '{"kind": "move", "place_id": "repair-shop"}', '{"temperature_c": null}', 1);
INSERT INTO events VALUES (1, 1, 'first_meeting', '{"text": "I met Ivo at the Repair shop.", "person_id": "ivo"}', NULL);
INSERT INTO encounters VALUES ('ivo', '2026-10-24T01:00:00.000000+00:00', '2026-10-24T09:00:00.000000+00:00', 3);
INSERT INTO publications VALUES (1, 'published', NULL);
INSERT INTO entries VALUES (1, 1, '2026-10-24T01:00:00.000000+00:00', 'I met Ivo at the Repair shop.');
