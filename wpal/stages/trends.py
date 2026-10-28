"""Item 4: monthly denominators for normalized term trends.

Annual volume varies about tenfold across the list's life, so a raw count of
messages mentioning a term mostly tracks how busy the list was. Trends divide
by these totals. They count every message, withdrawn posters included, since
an anonymous total names no one (PLAN.md decision 1).
"""

from __future__ import annotations


def run(db, ctx) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS month_totals;
        CREATE TABLE month_totals (
            year INTEGER, month INTEGER, messages INTEGER, threads INTEGER,
            words INTEGER, own_words INTEGER, PRIMARY KEY (year, month));
    """)
    has_fresh = "body_fresh" in {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    totals = {}
    for y, m, body, fresh, tid in db.execute(
            f"SELECT year, month, body, {'body_fresh' if has_fresh else 'body'}, thread_id FROM messages"):
        t = totals.setdefault((y, m), [0, set(), 0, 0])
        t[0] += 1
        t[1].add(tid)
        t[2] += len(body.split())
        t[3] += len((fresh or "").split())
    db.executemany("INSERT INTO month_totals VALUES (?,?,?,?,?,?)",
                   [(y, m, t[0], len(t[1]), t[2], t[3]) for (y, m), t in sorted(totals.items())])
    ctx.note(f"trends: denominators for {len(totals)} months")
