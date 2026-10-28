"""Item 8: the list's high-activity episodes, described, not judged.

Per thread: messages, distinct posters, duration, reply depth, the busiest
day's message count, and the share of posters who rarely post (fewer than
10 messages in all), since a thread that draws in rarely-heard voices is
reaching beyond the regulars.

Per week: volume against the median of the 26 weeks before it; the share of
the week's messages that are list business (genre "administrative" or
messages leaving, unsubscribing, or about moderation); and the share that
talk about the list itself ("this list", "listserv", "moderator", "civility",
"off-list"...).

Threads and weeks are ranked by the mean of their standardized (z) scores.
Labels say what happened ("82 messages from 41 people in 6 days"), never
who was right or what anyone was like.
"""

from __future__ import annotations

import collections
import re
import statistics
from datetime import datetime, timedelta

META = re.compile(r"\b(this list|the list|listserv|list ?serve|wpa-l|moderat\w*|civility|civil discourse|"
                  r"off[- ]list|unsubscrib\w*|leave the list|leaving the list|signing off|list norms|list culture|"
                  r"code of conduct|netiquette)\b", re.I)
LEAVE = re.compile(r"\b(unsubscrib\w*|leaving the list|leave the list|signing off|signoff)\b", re.I)
RARE = 10
TOP = 60


def week_of(iso: str) -> str:
    d = datetime.fromisoformat(iso).date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def z(values: list[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    m, s = statistics.fmean(values), statistics.pstdev(values) or 1.0
    return [(v - m) / s for v in values]


def run(db, ctx) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS thread_metrics;
        DROP TABLE IF EXISTS week_signals;
        DROP TABLE IF EXISTS flashpoints;
        CREATE TABLE thread_metrics (thread_id INTEGER PRIMARY KEY, root_uid TEXT, n INTEGER, posters INTEGER,
                                     hours REAL, depth INTEGER, peak_day INTEGER, rare_share REAL, score REAL);
        CREATE TABLE week_signals (week TEXT PRIMARY KEY, n INTEGER, baseline REAL, ratio REAL,
                                   admin_share REAL, meta_share REAL, score REAL);
        CREATE TABLE flashpoints (rank INTEGER, kind TEXT, ref TEXT, score REAL, label TEXT, start TEXT, finish TEXT,
                                  PRIMARY KEY (kind, rank));
    """)
    has_genre = "genre" in {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    total_posts = collections.Counter(dict(db.execute(
        "SELECT person_id, COUNT(*) FROM messages WHERE person_id IS NOT NULL GROUP BY person_id")))

    threads = collections.defaultdict(list)
    weeks = collections.defaultdict(lambda: [0, 0, 0])
    for tid, uid, parent, pid, sent, subject, fresh, genre in db.execute(
            f"SELECT thread_id, uid, parent_uid, person_id, sent_at, subject, "
            f"{'body_fresh' if 'body_fresh' in {r[1] for r in db.execute('PRAGMA table_info(messages)')} else 'body'}, "
            f"{'genre' if has_genre else 'NULL'} FROM messages WHERE sent_at IS NOT NULL"):
        threads[tid].append((uid, parent, pid, sent))
        w = weeks[week_of(sent)]
        w[0] += 1
        text = f"{subject} {(fresh or '')[:1500]}"
        w[1] += (genre == "administrative") or bool(LEAVE.search(text))
        w[2] += bool(META.search(text))

    roots = dict(db.execute("SELECT id, root_uid FROM threads"))
    rows = []
    for tid, msgs in threads.items():
        if len(msgs) < 5:
            continue
        msgs.sort(key=lambda m: m[3])
        people = {m[2] for m in msgs}
        t0, t1 = datetime.fromisoformat(msgs[0][3]), datetime.fromisoformat(msgs[-1][3])
        hours = (t1 - t0).total_seconds() / 3600
        parent = {m[0]: m[1] for m in msgs}
        depth = 0
        for u in parent:
            d, seen = 0, set()
            while parent.get(u) and u not in seen:
                seen.add(u)
                u = parent[u]
                d += 1
            depth = max(depth, d)
        days = collections.Counter(m[3][:10] for m in msgs)
        rare = sum(1 for p in people if total_posts.get(p, 0) < RARE) / len(people)
        rows.append([tid, roots.get(tid), len(msgs), len(people), round(hours, 1), depth, max(days.values()), round(rare, 3)])
    if rows:
        scores = list(zip(*(z([float(r[i]) for r in rows]) for i in (2, 3, 5, 6, 7))))
        # Duration counts against intensity: the same volume over fewer days is more of an episode.
        dz = z([-r[4] for r in rows])
        for r, s, d in zip(rows, scores, dz):
            r.append(round((sum(s) + 0.5 * d) / 5.5, 4))
    db.executemany("INSERT INTO thread_metrics VALUES (?,?,?,?,?,?,?,?,?)", rows)

    wk = sorted(weeks.items())
    out = []
    counts = [w[1][0] for w in wk]
    for i, (week, (n, admin, meta)) in enumerate(wk):
        base = statistics.median(counts[max(0, i - 26):i]) if i >= 8 else None
        ratio = n / base if base else None
        out.append([week, n, base, ratio, admin / n if n else 0, meta / n if n else 0])
    scored = [r for r in out if r[3] is not None and r[1] >= 20]
    if scored:
        zs = list(zip(z([r[3] for r in scored]), z([r[4] for r in scored]), z([r[5] for r in scored])))
        for r, s in zip(scored, zs):
            r.append(round(sum(s) / 3, 4))
    for r in out:
        if len(r) == 6:
            r.append(None)
    db.executemany("INSERT INTO week_signals VALUES (?,?,?,?,?,?,?)", out)

    flash = []
    for rank, r in enumerate(sorted(rows, key=lambda r: -r[8])[:TOP], start=1):
        span = f"{r[4] / 24:.0f} days" if r[4] >= 48 else f"{r[4]:.0f} hours"
        label = (f"{r[2]} messages from {r[3]} people in {span}; up to {r[6]} in one day; "
                 f"{r[7]:.0%} of participants rarely post")
        first = min(m[3] for m in threads[r[0]])
        last = max(m[3] for m in threads[r[0]])
        flash.append((rank, "thread", r[1], r[8], label, first, last))
    for rank, r in enumerate(sorted(scored, key=lambda r: -r[6])[:TOP], start=1):
        parts = [f"{r[1]} messages, {r[3]:.1f}× the usual week"]
        if r[4] > 0.05:
            parts.append(f"{r[4]:.0%} list business or departures")
        if r[5] > 0.1:
            parts.append(f"{r[5]:.0%} talk about the list itself")
        end = (datetime.fromisoformat(r[0]) + timedelta(days=6)).date().isoformat()
        flash.append((rank, "week", r[0], r[6], "; ".join(parts), r[0], end))
    db.executemany("INSERT INTO flashpoints VALUES (?,?,?,?,?,?,?)", flash)
    ctx.note(f"flashpoints: {len(rows):,} threads of 5+ and {len(scored):,} weeks scored")
