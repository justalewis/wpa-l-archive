"""Database access for the reader.

Three homes for data (PLAN.md):

- data/archive.db: derived. Rebuilt from scratch by wpal.build and opened
  read-only here.
- decisions/: curated judgments in git, read by the build.
- data/state.db: live and never rebuilt. Opt-out requests, suppressions,
  reader flags and saved corpora. It is migrated in place by
  `PRAGMA user_version`. (Before the research tools it was called
  requests.db; an existing requests.db is renamed on first start.)

Every reader query that names a poster or shows text goes through the temp
view `visible`, which drops suppressed rows.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path

# Each entry moves state.db from version i to i+1. Never edit a shipped step;
# add a new one.
STATE_MIGRATIONS = [
    # 1: the original opt-out tables.
    """
    CREATE TABLE IF NOT EXISTS suppression (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('poster', 'name')),
        key TEXT NOT NULL,
        label TEXT,
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
    """,
    # 2: suppression by person (item 1); saved corpora (item 2); thread flags
    # (item 3); a per-IP write log for rate limits.
    """
    CREATE TABLE suppression_new (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('poster', 'name', 'person')),
        key TEXT NOT NULL,
        label TEXT,
        request_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (kind, key)
    );
    INSERT INTO suppression_new SELECT * FROM suppression;
    DROP TABLE suppression;
    ALTER TABLE suppression_new RENAME TO suppression;

    CREATE TABLE corpora (
        id TEXT PRIMARY KEY,          -- 10 random characters, the public id
        name TEXT NOT NULL,
        description TEXT,
        query_json TEXT NOT NULL,     -- the filters as saved
        uids_json TEXT NOT NULL,      -- the frozen snapshot of message uids
        n INTEGER NOT NULL,
        build_id TEXT NOT NULL,       -- archive.db build the snapshot came from
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE thread_flags (
        id INTEGER PRIMARY KEY,
        thread_key TEXT NOT NULL,
        problem TEXT NOT NULL,        -- split | merged | wrong-parent | other
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        status TEXT NOT NULL DEFAULT 'new'  -- new | accepted | declined
    );
    CREATE TABLE write_log (
        ip_hash TEXT NOT NULL,
        action TEXT NOT NULL,
        at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX write_log_ip ON write_log(ip_hash, action, at);
    """,
]

VISIBLE_VIEW = """
CREATE TEMP VIEW IF NOT EXISTS visible AS
SELECT * FROM main.messages m
WHERE NOT EXISTS (SELECT 1 FROM req.suppression s WHERE s.kind = 'poster' AND s.key = m.poster_key)
  AND NOT EXISTS (SELECT 1 FROM req.suppression s WHERE s.kind = 'name' AND s.key = m.name_key)
  AND NOT EXISTS (SELECT 1 FROM req.suppression s WHERE s.kind = 'person' AND s.key = m.person_id)
"""


def archive_path() -> str:
    return os.environ.get("WPAL_DB", "data/archive.db")


def requests_path() -> str:
    """The state database. WPAL_REQUESTS_DB is the older name for WPAL_STATE_DB."""
    return os.environ.get("WPAL_STATE_DB") or os.environ.get("WPAL_REQUESTS_DB") or "data/state.db"


def init_requests(path: str | None = None) -> None:
    """Create or migrate the state database."""
    path = Path(path or requests_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = path.with_name("requests.db")
    if path.name == "state.db" and not path.exists() and legacy.exists():
        legacy.rename(path)
    con = sqlite3.connect(path)
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and con.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'optout_requests'").fetchone():
            version = 1  # a requests.db from before versioning
        for i, step in enumerate(STATE_MIGRATIONS[version:], start=version + 1):
            con.executescript(step)
            con.execute(f"PRAGMA user_version = {i}")
        con.commit()
    finally:
        con.close()


@contextlib.contextmanager
def state(path: str):
    """A short write transaction on the state database."""
    con = sqlite3.connect(path, timeout=10)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def connect(archive: str | None = None, requests: str | None = None) -> sqlite3.Connection:
    archive = archive or archive_path()
    requests = requests or requests_path()
    con = sqlite3.connect(f"file:{Path(archive).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("ATTACH DATABASE ? AS req", (requests,))
    con.execute(VISIBLE_VIEW)
    return con
