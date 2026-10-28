"""The whole dataset as one download: a zip of CSV files plus a README.

Built from the `visible` view, so withdrawn posters' messages are left out,
and cached next to the archive database. The cache key is the build id plus a
hash of the suppression list: a rebuild or a new withdrawal makes the next
download build a fresh bundle, and the old one is deleted.

CSV files are UTF-8 with a byte-order mark so Excel opens them with accents
and curly quotes intact. Message bodies can run past Excel's 32,767-character
cell limit; the README says so and points to a CSV-aware tool for those.

Run it ahead of time (on the server after a database upload) with
    python -m wpal.bulk [--base-url https://wpa-l-archive.fly.dev]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

from . import db as dbmod

STEM = "wpa-l-archive-data"
_lock = threading.Lock()
_building: set[str] = set()

MESSAGE_FIELDS = ["message_id", "thread_id", "parent_id", "poster_id", "poster_name", "date_utc", "year", "month",
                  "genre", "subject", "body", "body_own_words", "permalink"]

# (file, description, SQL). Every query reads through `visible` or names no one.
TABLES = [
    ("threads.csv", "One row per thread: the first message's id, subject, dates, visible message count, "
                    "how sure the threading is, and its topic.",
     """SELECT t.root_uid thread_id, t.subject, t.started_at, t.last_at, t.year, t.month,
               COUNT(v.id) n_messages, t.confidence_level threading_confidence, tt.topic_key
        FROM threads t JOIN visible v ON v.thread_id = t.id
        LEFT JOIN topic_threads tt ON tt.thread_key = t.root_uid
        GROUP BY t.id ORDER BY t.started_at"""),
    ("posters.csv", "People with 25 or more posts who have not withdrawn: id, name, dates and counts. "
                    "Everyone else appears only by name and id on their own messages.",
     """SELECT person_id poster_id, display_name name, first_at, last_at, n_posts, n_started, n_replies
        FROM persons WHERE public = 1 AND person_id NOT IN (SELECT pid FROM hidden_persons)
        ORDER BY n_posts DESC"""),
    ("monthly_totals.csv", "Messages, threads and words per month (all messages, withdrawn ones included as counts).",
     "SELECT year, month, messages, threads, words, own_words FROM month_totals ORDER BY year, month"),
    ("genre_by_year.csv", "Messages per genre per year.",
     "SELECT year, genre, n messages FROM genre_year ORDER BY year, genre"),
    ("topics.csv", "Topics from the topic model, with their labels and top terms.",
     """SELECT t.topic_key, t.label, t.labeled_by, t.n_threads, t.peak_year,
               (SELECT group_concat(term, '; ') FROM (SELECT term FROM topic_terms x
                 WHERE x.topic_key = t.topic_key ORDER BY rank LIMIT 10)) top_terms
        FROM topics t ORDER BY t.n_threads DESC"""),
    ("topic_by_month.csv", "Threads per topic per month, and the topic's share of that month's threads.",
     "SELECT topic_key, year, month, n threads, share FROM topic_months ORDER BY topic_key, year, month"),
    ("cited_works.csv", "Scholarly works the list mentioned, matched to Pinakes (accepted matches only).",
     """SELECT work_id pinakes_id, title, authors, year, journal, doi, n_mentions, n_messages, first_year
        FROM cited_works ORDER BY n_messages DESC"""),
    ("citations.csv", "Each accepted mention of a work: which message, which work.",
     """SELECT m.uid message_id, m.year, m.kind, m.raw text_as_written, m.work_id pinakes_id, m.work_title
        FROM mentions m JOIN visible v ON v.uid = m.uid WHERE m.status = 'accepted' ORDER BY m.year"""),
    ("institutions.csv", "IPEDS institutions named in signatures and job ads, with Carnegie type.",
     "SELECT unitid, name, city, state, carnegie_type, lat, lon FROM institutions ORDER BY name"),
    ("institutions_by_era.csv", "Posts and people per institution per era, by poster affiliation.",
     "SELECT era, unitid, posts, people FROM institution_era ORDER BY era, posts DESC"),
    ("jobs.csv", "Job ads: message, institution, title and rank as read from the ad.",
     """SELECT j.uid message_id, j.posted_at, j.year, j.institution, j.title, j.rank_type
        FROM jobs j JOIN visible v ON v.uid = j.uid ORDER BY j.posted_at"""),
    ("events.csv", "The field's events used as an overlay on the charts.",
     "SELECT date, end_date, label, category, source FROM events ORDER BY date"),
]


def bundle_dir(archive: str) -> Path:
    return Path(os.environ.get("WPAL_EXPORT_DIR") or Path(archive).resolve().parent / "exports")


def bundle_key(con) -> str:
    build = (con.execute("SELECT value FROM meta WHERE key IN ('build_id', 'built_at') ORDER BY key LIMIT 1")
             .fetchone() or [""])[0]
    stages = (con.execute("SELECT value FROM meta WHERE key = 'stages_at'").fetchone() or [""])[0]
    h = hashlib.sha256(f"{build}|{stages}".encode())
    for kind, key in con.execute("SELECT kind, key FROM req.suppression ORDER BY kind, key"):
        h.update(f"\n{kind}:{key}".encode())
    return h.hexdigest()[:12]


def bundle_path(archive: str, key: str) -> Path:
    return bundle_dir(archive) / f"{STEM}-{key}.zip"


def _csv_entry(zf, name, header, rows) -> int:
    n = 0
    with zf.open(name, "w", force_zip64=True) as raw:
        out = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        w = csv.writer(out)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if x is None else x for x in r])
            n += 1
        out.flush()
        out.detach()
    return n


def write_bundle(archive: str, requests: str, base_url: str) -> Path:
    """Build the zip for the current key (unless it exists) and delete older ones."""
    con = dbmod.connect(archive, requests)
    key = bundle_key(con)
    dest = bundle_path(archive, key)
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{os.getpid()}-{threading.get_ident()}.partial")  # one per builder
    base = base_url.rstrip("/")
    counts = {}
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        fresh = "body_fresh" in {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        # One file per year: the whole run is over 800 MB of text, too much to open in a spreadsheet.
        years = [r[0] for r in con.execute("SELECT DISTINCT year FROM main.messages ORDER BY year")]
        n_messages = 0
        for year in years:
            rows = con.execute(f"""
                SELECT v.uid, t.root_uid, v.parent_uid, v.person_id, v.from_name, v.sent_at, v.year, v.month,
                       v.genre, v.subject, v.body, {'v.body_fresh' if fresh else "''"}
                FROM visible v JOIN threads t ON t.id = v.thread_id
                WHERE v.year = ? ORDER BY v.month, v.sent_at, v.id""", (year,))
            n_messages += _csv_entry(zf, f"messages/messages-{year}.csv", MESSAGE_FIELDS,
                                     (tuple(r) + (f"{base}/m/{r[0]}",) for r in rows))
        counts["messages/"] = n_messages
        described = [("messages/", f"Every message still public, one file per year ({years[0]}-{years[-1]}): one row "
                                   "per message, bodies with addresses removed.")]
        for name, desc, sql in TABLES:
            try:
                cur = con.execute(sql)
            except sqlite3.OperationalError:  # a stage that didn't run in this build
                continue
            counts[name] = _csv_entry(zf, name, [d[0] for d in cur.description], cur)
            described.append((name, desc))
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        zf.writestr("README.txt", _readme(meta, base, described, counts))
    con.close()
    if dest.exists():  # another builder finished first
        tmp.unlink(missing_ok=True)
        return dest
    os.replace(tmp, dest)
    for old in [*dest.parent.glob(f"{STEM}-*.zip"), *dest.parent.glob(f"{STEM}-*.partial")]:
        if old != dest and not (old.suffix == ".partial" and time.time() - old.stat().st_mtime < 3600):
            try:
                old.unlink()
            except OSError:  # still being sent to someone; next build clears it
                pass
    return dest


def build_in_background(archive: str, requests: str, base_url: str, key: str) -> bool:
    """Start one build for this key if none is running. True if one is running now."""
    with _lock:
        if key in _building:
            return True
        _building.add(key)

    def work():
        try:
            write_bundle(archive, requests, base_url)
        finally:
            with _lock:
                _building.discard(key)

    threading.Thread(target=work, daemon=True, name=f"bulk-{key}").start()
    return True


def _readme(meta, base, described, counts) -> str:
    lines = [
        "WPA-L Archive Reader: the full dataset",
        "=" * 38,
        "",
        f"Source: Internet Archive item WPA-L-1993-2021-Complete-Uncompresse (341 monthly LISTSERV logs, 1993-2021).",
        f"Build: {meta.get('build_id') or meta.get('built_at', '')}"
        + (f"; stages re-run {meta['stages_at']}" if meta.get("stages_at") else ""),
        f"Bundle made: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"Reader: {base}/   Field notes: {base}/data   Ethics: {base}/ethics",
        "",
        "Files",
        "-----",
    ]
    for name, desc in described:
        lines.append(f"{name}  ({counts.get(name, 0):,} rows)")
        lines.append(f"    {desc}")
    lines += [
        "",
        "Ids",
        "---",
        "message_id is stable across builds; a message's page is " + base + "/m/{message_id}.",
        "thread_id is the id of the thread's first message; its page is " + base + "/t/{thread_id}.",
        "poster_id groups one person's addresses and name variants. topic_key is the id of a topic's most",
        "representative thread. pinakes_id is a work in Pinakes; unitid is an IPEDS institution.",
        "",
        "What is left out",
        "----------------",
        "Email addresses (removed everywhere, including inside message text), attachments, and every message",
        "from a poster who asked to be withdrawn. Monthly and genre totals still count withdrawn messages,",
        "anonymously. If you publish from this data, check message ids against the API first",
        f"({base}/api/v1/messages/{{id}}): a 404 means the message has since been withdrawn.",
        "",
        "Opening the files",
        "-----------------",
        "UTF-8 CSV with a byte-order mark. Excel, Google Sheets, LibreOffice, R and pandas all read them.",
        "Messages come one file per year (2 to 60 MB each). Excel cuts any cell longer than 32,767",
        "characters, so a few very long message bodies are truncated there; R (readr) and Python (pandas)",
        "read them whole. To combine the years in pandas:",
        "    pd.concat(pd.read_csv(f) for f in sorted(glob.glob('messages/*.csv')))",
        "Python's own csv module needs csv.field_size_limit(10**7) for the longest bodies.",
        "",
        "Terms",
        "-----",
        "For research and teaching. Don't republish contact details found in messages, honor withdrawals",
        "in anything you derive from this data, and cite the archive and the build.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=dbmod.archive_path())
    ap.add_argument("--requests", default=dbmod.requests_path())
    ap.add_argument("--base-url", default="https://wpa-l-archive.fly.dev")
    a = ap.parse_args()
    t0 = time.time()
    path = write_bundle(a.db, a.requests, a.base_url)
    print(f"{path} ({path.stat().st_size / 1e6:.0f} MB) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
