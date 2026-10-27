"""Database access for the reader.

The archive database (data/archive.db) is rebuilt by wpal.build and opened
read-only. Opt-out requests and suppressions live in data/requests.db, which a
rebuild never touches. Every reader query goes through the temporary view
`visible`, which drops messages from suppressed posters; nothing reads the
messages table directly.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppression (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('poster', 'name')),
    key TEXT NOT NULL,
    label TEXT,              -- what the operator typed, for the record
    request_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (kind, key)
);
CREATE TABLE IF NOT EXISTS optout_requests (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    name_as_posted TEXT NOT NULL,
    other_names TEXT,
    example_links TEXT,
    contact TEXT NOT NULL,   -- for verification only; never shown in the reader
    relationship TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'new'  -- new | verified | applied | declined
);
"""

VISIBLE_VIEW = """
CREATE TEMP VIEW IF NOT EXISTS visible AS
SELECT * FROM main.messages m
WHERE NOT EXISTS (SELECT 1 FROM req.suppression s WHERE s.kind = 'poster' AND s.key = m.poster_key)
  AND NOT EXISTS (SELECT 1 FROM req.suppression s WHERE s.kind = 'name' AND s.key = m.name_key)
"""


def archive_path() -> str:
    return os.environ.get("WPAL_DB", "data/archive.db")


def requests_path() -> str:
    return os.environ.get("WPAL_REQUESTS_DB", "data/requests.db")


def init_requests(path: str | None = None) -> None:
    path = path or requests_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(REQUESTS_SCHEMA)
    con.commit()
    con.close()


def connect(archive: str | None = None, requests: str | None = None) -> sqlite3.Connection:
    archive = archive or archive_path()
    requests = requests or requests_path()
    con = sqlite3.connect(f"file:{Path(archive).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("ATTACH DATABASE ? AS req", (requests,))
    con.execute(VISIBLE_VIEW)
    return con
