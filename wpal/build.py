"""Parse every monthly log, rebuild threads, and load SQLite with FTS5.

    python -m wpal.build [--raw data/raw] [--db data/archive.db] [--only 1993]
    python -m wpal.build --stages identity     (re-run stages on the existing DB)

The raw logs are read, never written. The database is rebuilt from scratch on
each run; opt-out requests and suppressions live in a separate file
(data/requests.db) so a rebuild never loses them.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import stages
from .parse import parse_file
from .threads import apply_overrides, build_threads

# Covering indexes for the reader. Every query passes through the `visible`
# view, which needs poster_key and name_key; without these, counting messages
# per month reads every row, bodies included (40 s on a cold Fly volume).
INDEXES = """
CREATE INDEX IF NOT EXISTS messages_cover
    ON messages(year, month, sent_at, thread_id, poster_key, name_key, person_id, from_name);
CREATE INDEX IF NOT EXISTS messages_parent ON messages(parent_uid, sent_at);
CREATE INDEX IF NOT EXISTS messages_sent ON messages(sent_at);
"""

SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    thread_id INTEGER,
    parent_uid TEXT,
    thread_method TEXT,
    link_method TEXT,       -- how the reply edge to parent_uid was found (item 3)
    sent_at TEXT,           -- ISO 8601, UTC; NULL when the Date header failed
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    dated INTEGER NOT NULL, -- 0 when year/month come from the file name
    date_raw TEXT,
    from_name TEXT NOT NULL,
    name_key TEXT NOT NULL, -- lowercased, sorted name tokens, for opt-out matching
    poster_key TEXT NOT NULL,
    person_id TEXT,         -- canonical poster (wpal/stages/identity.py)
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    attachments TEXT NOT NULL,
    source_file TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    problems TEXT NOT NULL
);
CREATE INDEX messages_ym ON messages(year, month, sent_at);
CREATE INDEX messages_thread ON messages(thread_id, sent_at);
CREATE INDEX messages_poster ON messages(poster_key);
CREATE INDEX messages_name ON messages(name_key);
""" + INDEXES + """

CREATE TABLE threads (
    id INTEGER PRIMARY KEY,
    root_uid TEXT NOT NULL,
    subject TEXT NOT NULL,
    started_at TEXT,
    last_at TEXT,
    year INTEGER,
    month INTEGER,
    n_messages INTEGER NOT NULL
);
CREATE INDEX threads_ym ON threads(year, month, started_at);
CREATE UNIQUE INDEX threads_root ON threads(root_uid);
CREATE TABLE thread_redirects (old_key TEXT PRIMARY KEY, new_key TEXT NOT NULL);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    subject, body, content='messages', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

FILE_RE = re.compile(r"(\d{4})-(\d{2})\.txt$")
LEAK = re.compile(
    r"[\w.+-]{2,}(?:@|%(?:25)*40|-(?:25)*40|\*(?:25)*40|&#64;)[\w-]+\.(?:edu|com|org|net|gov|ca|uk|au|us|bitnet)\b"
    r"|[\w.-]{2,}\s+(?:at|\(at\)|\[at\])\s+[\w-]+\s+(?:dot|\(dot\)|\[dot\])\s+(?:edu|com|org|net)\b",
    re.I,
)


def load_salt(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    salt = secrets.token_bytes(32)
    path.write_bytes(salt)
    return salt


def read_csv(path: Path) -> list[dict]:
    """Rows of a decisions/ file; '#' in the first column comments a row out."""
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if not (next(iter(r.values()), "") or "").startswith("#")]


def _work(args):
    path, rel, salt = args
    return rel, parse_file(path, rel, salt)


def name_key(name: str) -> str:
    return " ".join(sorted(name.replace(",", " ").replace(".", " ").lower().split()))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--db", default="data/archive.db")
    ap.add_argument("--salt", default="data/poster.salt")
    ap.add_argument("--only", help="restrict to file names containing this string")
    ap.add_argument("--report", default="reports/coverage.md")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--decisions", default="decisions")
    ap.add_argument("--reference", default="data/reference")
    ap.add_argument("--stages", help="comma-separated: re-run these stages (and later ones) on the existing DB")
    ap.add_argument("--skip-stages", default="", help="comma-separated stages to leave out (e.g. topics)")
    a = ap.parse_args(argv)
    a.skip_stages = [s for s in a.skip_stages.split(",") if s]
    if a.stages:
        return rerun_stages(a)

    raw = Path(a.raw)
    files = sorted(p for p in raw.rglob("*.txt") if FILE_RE.search(p.name))
    if a.only:
        files = [p for p in files if a.only in p.name]
    if not files:
        sys.exit("no monthly files found under " + str(raw))
    salt = load_salt(Path(a.salt))

    tmp = Path(a.db + ".building")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")

    t0 = time.time()
    light = []  # messages with bodies dropped once stored
    names_by_addr: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    seen_uids: collections.Counter = collections.Counter()
    per_file = {}
    jobs = [(str(p), p.name, salt) for p in files]
    with ProcessPoolExecutor(a.workers) as ex:
        for rel, msgs in ex.map(_work, jobs, chunksize=1):
            y, mo = map(int, FILE_RE.search(rel).groups())
            per_file[rel] = len(msgs)
            rows = []
            for m in msgs:
                seen_uids[m.uid] += 1
                if seen_uids[m.uid] > 1:
                    m.problems.append("duplicate-id")
                    m.uid = f"{m.uid}-{seen_uids[m.uid]}"
                if m._address and m.from_name:
                    names_by_addr[m._address][m.from_name] += 1
                dated = m.date is not None
                # Browse by the log's month unless the Date header is within a month
                # of it; a sender's wrong clock otherwise files a 2006 post under 2003.
                by, bm = y, mo
                if dated and abs((m.date.year * 12 + m.date.month) - (y * 12 + mo)) <= 1:
                    by, bm = m.date.year, m.date.month
                elif dated:
                    m.problems.append("date-outside-log")
                rows.append((
                    m.uid,
                    m.date.isoformat() if dated else None,
                    by,
                    bm,
                    int(dated),
                    m.date_raw,
                    m.from_name,
                    m.poster_key,
                    m.subject,
                    m.body,
                    json.dumps(m.attachments),
                    rel,
                    m.ordinal,
                    json.dumps(m.problems),
                ))
                m.body = ""
                light.append(m)
            db.executemany(
                "INSERT INTO messages (uid, sent_at, year, month, dated, date_raw, from_name,"
                " name_key, poster_key, subject, body, attachments, source_file, ordinal, problems)"
                " VALUES (?,?,?,?,?,?,?,'',?,?,?,?,?,?,?)",
                rows,
            )
            print(f"  {rel}: {len(msgs)} messages  ({time.time() - t0:.0f}s)", flush=True)

    # Recover a display name for posters whose From line carried only an address.
    recovered = 0
    for m in light:
        if not m.from_name and m._address and names_by_addr.get(m._address):
            m.from_name = names_by_addr[m._address].most_common(1)[0][0]
            recovered += 1
        if not m.from_name:
            m.from_name = "Unnamed poster"
        m._address = ""  # addresses go no further
    names_by_addr.clear()

    tr = build_threads(light)
    overrides = read_csv(Path(a.decisions) / "thread_overrides.csv")
    when = {m.uid: (m.date.isoformat() if m.date else "9999", m.source_file, m.ordinal) for m in light}
    for problem in apply_overrides(tr, overrides, when):
        print(f"  thread override skipped: {problem}")
    db.executemany(
        "UPDATE messages SET from_name=?, name_key=?, thread_id=?, parent_uid=?, thread_method=?, link_method=? WHERE uid=?",
        [(m.from_name, name_key(m.from_name), tr.thread_of[m.uid], tr.parent_of[m.uid],
          tr.method_of[m.uid], tr.link_of[m.uid], m.uid) for m in light],
    )
    db.executemany("INSERT OR REPLACE INTO thread_redirects VALUES (?, ?)", tr.redirects.items())
    subj = {m.uid: m.subject for m in light}
    db.executemany(
        "INSERT INTO threads (id, root_uid, subject, n_messages) VALUES (?,?,?,?)",
        [(t.tid, t.root, subj.get(t.root) or "(no subject)", len(t.members)) for t in tr.threads.values()],
    )
    db.executescript("""
        UPDATE threads SET
          started_at = (SELECT MIN(sent_at) FROM messages WHERE thread_id = threads.id),
          last_at    = (SELECT MAX(sent_at) FROM messages WHERE thread_id = threads.id),
          year  = (SELECT year  FROM messages WHERE uid = threads.root_uid),
          month = (SELECT month FROM messages WHERE uid = threads.root_uid);
        INSERT INTO messages_fts(messages_fts) VALUES ('rebuild');
        INSERT INTO messages_fts(messages_fts) VALUES ('optimize');
    """)

    db.commit()
    ctx = stages.Context(decisions=Path(a.decisions), previous_db=Path(a.db), reference=Path(a.reference))
    stages.run(db, ctx, skip=a.skip_stages)

    report = coverage(db, per_file, recovered)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("INSERT INTO meta VALUES ('built_at', ?)", (stamp,))
    db.execute("INSERT INTO meta VALUES ('build_id', ?)", (stamp,))
    db.execute("INSERT INTO meta VALUES ('coverage', ?)", (json.dumps(report),))
    db.commit()
    db.close()
    _swap(tmp, Path(a.db))
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    write_report(report, Path(a.report))
    print(f"done in {time.time() - t0:.0f}s -> {a.db}")


def _swap(tmp: Path, dst: Path) -> None:
    """Move the finished build into place. Windows can hold a just-written
    file briefly (antivirus, indexer), so retry for a while before failing."""
    for attempt in range(30):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(2)


def rerun_stages(a):
    """Run stages on a copy of the finished database, then swap it in."""
    t0 = time.time()
    src = Path(a.db)
    tmp = Path(a.db + ".building")
    shutil.copyfile(src, tmp)
    db = sqlite3.connect(tmp)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    ctx = stages.Context(decisions=Path(a.decisions), previous_db=src, reference=Path(a.reference))
    stages.run(db, ctx, only=a.stages.split(","), skip=a.skip_stages)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('stages_at', ?)", (stamp,))
    db.commit()
    db.close()
    _swap(tmp, src)
    print(f"stages done in {time.time() - t0:.0f}s -> {src}")


def coverage(db, per_file, recovered):
    rows = db.execute("""
        SELECT year,
               COUNT(*),
               SUM(dated),
               SUM(thread_id IN (SELECT id FROM threads WHERE n_messages > 1)),
               SUM(thread_method = 'header'),
               SUM(thread_method = 'sibling'),
               SUM(thread_method = 'subject'),
               SUM(from_name = 'Unnamed poster'),
               SUM(problems LIKE '%html-only%'),
               SUM(problems LIKE '%no-text-part%'),
               SUM(attachments != '[]')
        FROM messages GROUP BY year ORDER BY year
    """).fetchall()
    years = [dict(zip(
        ["year", "messages", "dated", "threaded", "via_header", "via_sibling", "via_subject",
         "unnamed", "html_only", "no_text", "with_attachments"], r)) for r in rows]
    problems = collections.Counter()
    examples = collections.defaultdict(list)
    for uid, src, probs in db.execute("SELECT uid, source_file, problems FROM messages WHERE problems != '[]'"):
        for p in json.loads(probs):
            problems[p] += 1
            if len(examples[p]) < 5:
                examples[p].append(f"{src} {uid}")
    n_threads = db.execute("SELECT COUNT(*), SUM(n_messages > 1) FROM threads").fetchone()
    # A second, looser net over what was stored. Hits are listed by id only.
    leaks, leak_examples = 0, []
    for uid, src, text in db.execute("SELECT uid, source_file, subject || ' ' || from_name || ' ' || body FROM messages"):
        if LEAK.search(text):
            leaks += 1
            if len(leak_examples) < 20:
                leak_examples.append(f"{src} {uid}")
    return {
        "possible_addresses": leaks,
        "possible_address_examples": leak_examples,
        "files": len(per_file),
        "empty_files": sorted(f for f, n in per_file.items() if n == 0),
        "years": years,
        "problems": dict(problems.most_common()),
        "problem_examples": examples,
        "names_recovered_from_other_posts": recovered,
        "threads": n_threads[0],
        "threads_with_replies": n_threads[1],
    }


def write_report(r, path: Path):
    lines = [
        "# Parse coverage",
        "",
        f"Files parsed: {r['files']}. Threads: {r['threads']:,} ({r['threads_with_replies']:,} with replies).",
        f"Display names recovered from the same poster's other messages: {r['names_recovered_from_other_posts']:,}.",
        "",
        "| Year | Messages | Dated | In a thread | Header link | Sibling link | Subject link | Unnamed | HTML only | No text | Attachments |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    tot = collections.Counter()
    for y in r["years"]:
        for k, v in y.items():
            if k != "year":
                tot[k] += v or 0
        lines.append("| {year} | {messages:,} | {dated:,} | {threaded:,} | {via_header:,} | {via_sibling:,} | "
                     "{via_subject:,} | {unnamed:,} | {html_only:,} | {no_text:,} | {with_attachments:,} |".format(
                         **{k: (v or 0) for k, v in y.items()}))
    lines.append("| **All** | **{messages:,}** | {dated:,} | {threaded:,} | {via_header:,} | {via_sibling:,} | "
                 "{via_subject:,} | {unnamed:,} | {html_only:,} | {no_text:,} | {with_attachments:,} |".format(**tot))
    lines += ["", "## Problems flagged", "", "| Problem | Count | Examples (file, id) |", "|---|---:|---|"]
    for p, n in r["problems"].items():
        lines.append(f"| {p} | {n:,} | {'; '.join(r['problem_examples'][p])} |")
    lines += ["", "## Leak audit", "",
              f"Messages where a looser address pattern still matches after scrubbing: {r['possible_addresses']:,}."]
    if r["possible_address_examples"]:
        lines.append("First examples (file, id): " + "; ".join(r["possible_address_examples"]))
    if r["empty_files"]:
        lines += ["", "Files with no messages: " + ", ".join(r["empty_files"])]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
