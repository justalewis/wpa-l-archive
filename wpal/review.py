"""Review queues. Accepted decisions are appended to decisions/, so the next
build applies them and every later build keeps them.

    python -m wpal.review merges                 # candidates to join or split
    python -m wpal.review merge KEY_A KEY_B "why"
    python -m wpal.review split KEY "why"        # keep KEY out of name merges
    python -m wpal.review flags                  # thread flags from readers
    python -m wpal.review thread-merge ROOT_A ROOT_B "why"
    python -m wpal.review thread-split UID "why" # UID starts its own thread
    python -m wpal.review flag-status ID accepted|declined
    python -m wpal.review citations [--accept ID | --reject ID]
    python -m wpal.review institutions [--match NAME UNITID | --reject NAME]

Poster keys are salted hashes; nothing here prints an address.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from .db import archive_path, requests_path

DECISIONS = Path("decisions")


def append(name: str, header: list[str], row: list[str]) -> None:
    path = DECISIONS / name
    new = not path.exists()
    DECISIONS.mkdir(exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        w.writerow(row)
    print(f"recorded in {path}; rebuild (python -m wpal.build --stages identity) to apply")


def ro(path):
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--db", default=archive_path())
    ap.add_argument("--state", default=requests_path())
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--accept")
    ap.add_argument("--reject")
    ap.add_argument("--match", nargs=2)
    a = ap.parse_args(argv)
    db = ro(a.db)

    if a.cmd == "merges":
        for r in db.execute("""SELECT * FROM merge_candidates ORDER BY reason != 'nickname', confidence DESC
                               LIMIT ?""", (a.limit,)):
            print(f"{r['reason']:<30} {r['name_a']!r:<32} {r['name_b']!r:<32} {r['poster_key_a']} {r['poster_key_b']}")
    elif a.cmd == "merge":
        ka, kb, *why = a.args
        append("poster_merges.csv", ["poster_key_a", "poster_key_b", "note"], [ka, kb, " ".join(why)])
    elif a.cmd == "split":
        k, *why = a.args
        append("poster_splits.csv", ["poster_key", "note"], [k, " ".join(why)])
    elif a.cmd == "flags":
        st = ro(a.state)
        for r in st.execute("SELECT * FROM thread_flags WHERE status = 'new' ORDER BY created_at"):
            print(f"#{r['id']} {r['created_at']} /t/{r['thread_key']} {r['problem']}: {r['note'] or ''}")
    elif a.cmd == "thread-merge":
        ra, rb, *why = a.args
        append("thread_overrides.csv", ["action", "uid", "target", "note"], ["merge", rb, ra, " ".join(why)])
    elif a.cmd == "thread-split":
        uid, *why = a.args
        append("thread_overrides.csv", ["action", "uid", "target", "note"], ["split", uid, "", " ".join(why)])
    elif a.cmd == "flag-status":
        fid, status = a.args
        con = sqlite3.connect(a.state)
        con.execute("UPDATE thread_flags SET status = ? WHERE id = ?", (status, int(fid)))
        con.commit()
    elif a.cmd == "citations":
        if a.accept or a.reject:
            r = db.execute("SELECT * FROM mentions WHERE id = ?", (int(a.accept or a.reject),)).fetchone()
            append("citation_matches.csv", ["uid", "raw", "work_id", "decision"],
                   [r["uid"], r["raw"], r["work_id"] or "", "accept" if a.accept else "reject"])
        else:
            for r in db.execute("SELECT * FROM mentions WHERE status = 'review' ORDER BY confidence DESC LIMIT ?", (a.limit,)):
                print(f"#{r['id']} {r['confidence']:.2f} {r['raw']!r} -> {r['work_title']!r}")
    elif a.cmd == "institutions":
        if a.match:
            append("institution_matches.csv", ["name", "unitid"], a.match)
        elif a.reject:
            append("institution_matches.csv", ["name", "unitid"], [a.reject, "none"])
        else:
            for r in db.execute("SELECT name, n FROM unmatched_institutions ORDER BY n DESC LIMIT ?", (a.limit,)):
                print(f"{r['n']:>5}  {r['name']}")
    else:
        ap.error(f"unknown command {a.cmd}")


if __name__ == "__main__":
    main()
