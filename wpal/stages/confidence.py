"""Item 3: how far to trust each rebuilt thread.

Every reply edge carries the method that found it (wpal/threads.py):

  message-id      the reply's In-Reply-To or References header names its parent
  wrote-line      the reply's "X wrote:" line names an earlier poster in the thread
  in-reply-to     the reply shares an In-Reply-To with an earlier reply (a sibling)
  subject-window  only the normalized subject and the time window joined it
  override        a reviewed correction (decisions/thread_overrides.csv)

A thread's score weighs the share of strong edges (message-id and override
count fully, wrote-line half), how generic its subject is (how many threads
in the corpus share it), and how long it ran. The weights are a judgment,
recorded here and on the thread page, not a fitted model.

Split candidates: threads with the same normalized subject that the 30-day
window kept apart but whose posters overlap, offered for review as possible
continuations of one discussion.
"""

from __future__ import annotations

import collections
import math
from datetime import datetime

from ..parse import normalize_subject

STRONG = {"message-id": 1.0, "override": 1.0, "wrote-line": 0.5, "in-reply-to": 0.25, "subject-window": 0.0}
WEIGHTS = {"edges": 0.6, "subject": 0.25, "spread": 0.15}
LEVELS = [(0.75, "high"), (0.45, "medium"), (0.0, "low")]


def run(db, ctx) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS edges;
        CREATE TABLE edges (child_uid TEXT PRIMARY KEY, parent_uid TEXT, link_method TEXT,
                            child_person TEXT, parent_person TEXT, year INTEGER);
        INSERT INTO edges
            SELECT c.uid, c.parent_uid, COALESCE(c.link_method, 'subject-window'), c.person_id, p.person_id, c.year
            FROM messages c JOIN messages p ON p.uid = c.parent_uid
            WHERE c.parent_uid IS NOT NULL;
        CREATE INDEX edges_parent ON edges(parent_uid);
        CREATE INDEX edges_people ON edges(child_person, parent_person);
    """)
    from . import add_column
    for col, decl in [("confidence", "REAL"), ("confidence_level", "TEXT"), ("confidence_reason", "TEXT"),
                      ("subject_key", "TEXT")]:
        add_column(db, "threads", col, decl)

    keys = {tid: normalize_subject(subj) for tid, subj in db.execute("SELECT id, subject FROM threads")}
    freq = collections.Counter(keys.values())
    methods = collections.defaultdict(collections.Counter)
    for tid, method in db.execute(
            "SELECT m.thread_id, e.link_method FROM edges e JOIN messages m ON m.uid = e.child_uid"):
        methods[tid][method] += 1

    rows = []
    levels = collections.Counter()
    for tid, n, started, last in db.execute("SELECT id, n_messages, started_at, last_at FROM threads"):
        key = keys[tid]
        if n < 2:
            rows.append((None, "single", "One message; nothing was joined.", key, tid))
            continue
        m = methods[tid]
        edges = sum(m.values()) or 1
        strong = sum(STRONG.get(k, 0) * v for k, v in m.items()) / edges
        generic = min(1.0, math.log(freq[key] or 1) / math.log(50)) if key else 1.0
        days = 0.0
        if started and last:
            days = (datetime.fromisoformat(last) - datetime.fromisoformat(started)).total_seconds() / 86400
        spread = min(1.0, days / 60)
        score = WEIGHTS["edges"] * strong + WEIGHTS["subject"] * (1 - generic) + WEIGHTS["spread"] * (1 - spread)
        level = next(name for floor, name in LEVELS if score >= floor)
        levels[level] += 1
        rows.append((round(score, 3), level, reason(m, edges, generic, days), key, tid))
    db.executemany("UPDATE threads SET confidence = ?, confidence_level = ?, confidence_reason = ?, subject_key = ? "
                   "WHERE id = ?", rows)
    db.execute("CREATE INDEX IF NOT EXISTS threads_subject ON threads(subject_key, started_at)")
    _split_candidates(db)
    ctx.note("confidence: " + ", ".join(f"{k} {v:,}" for k, v in levels.most_common()))


def reason(m: collections.Counter, edges: int, generic: float, days: float) -> str:
    share = m["message-id"] / edges
    if share >= 0.9:
        how = "Linked by message headers"
    elif share > 0:
        how = f"Partly linked by message headers ({share:.0%} of replies); the rest rebuilt from subject lines"
    elif m["wrote-line"] / edges >= 0.5:
        how = "Rebuilt from subject lines, with most replies matched to “X wrote:” attributions"
    else:
        how = "Rebuilt from subject lines"
    extras = []
    if generic > 0.6:
        extras.append("the subject is common across the list")
    if days > 45:
        extras.append(f"it ran {days:.0f} days")
    return how + ("; " + " and ".join(extras) if extras else "") + "."


def _split_candidates(db) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS split_candidates;
        CREATE TABLE split_candidates (a_key TEXT, b_key TEXT, subject_key TEXT, shared_people INTEGER, gap_days REAL);
    """)
    by_subject = collections.defaultdict(list)
    for tid, root, key, started, last in db.execute(
            "SELECT id, root_uid, subject_key, started_at, last_at FROM threads "
            "WHERE subject_key != '' AND n_messages >= 2 ORDER BY subject_key, started_at"):
        by_subject[key].append((tid, root, started, last))
    people = {}
    rows = []
    for key, ts in by_subject.items():
        if len(ts) < 2 or len(ts) > 20:  # a subject on 20+ threads is generic, not one discussion
            continue
        for (ta, ra, sa, la), (tb, rb, sb, lb) in zip(ts, ts[1:]):
            if not (la and sb):
                continue
            gap = (datetime.fromisoformat(sb) - datetime.fromisoformat(la)).total_seconds() / 86400
            if gap > 120:
                continue
            for t in (ta, tb):
                if t not in people:
                    people[t] = {r[0] for r in db.execute("SELECT DISTINCT person_id FROM messages WHERE thread_id = ?", (t,))}
            shared = len(people[ta] & people[tb])
            if shared >= 2:
                rows.append((ra, rb, key, shared, round(gap, 1)))
    db.executemany("INSERT INTO split_candidates VALUES (?,?,?,?,?)", rows)
