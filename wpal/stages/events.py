"""Item 11: the field's calendar, from decisions/events.yaml, for chart overlays."""

from __future__ import annotations

import yaml

CATEGORIES = {"cccc": "CCCC convention", "cwpa": "CWPA", "statement": "Position statement",
              "news": "News and policy", "list": "List administration"}


def load(path) -> list[dict]:
    if not path.exists():
        return []
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = []
    for r in rows:
        d = str(r["date"])
        parts = d.split("-")
        out.append({"date": d, "end": str(r.get("end") or ""), "year": int(parts[0]), "month": int(parts[1]),
                    "day": int(parts[2]) if len(parts) > 2 else None, "label": r["label"],
                    "category": r.get("category", "news"), "source": r.get("source", "")})
    return out


def run(db, ctx) -> None:
    events = load(ctx.decisions / "events.yaml")
    db.executescript("""
        DROP TABLE IF EXISTS events;
        CREATE TABLE events (id INTEGER PRIMARY KEY, date TEXT, end_date TEXT, year INTEGER, month INTEGER,
                             day INTEGER, label TEXT, category TEXT, source TEXT);
    """)
    db.executemany("INSERT INTO events (date, end_date, year, month, day, label, category, source) VALUES (?,?,?,?,?,?,?,?)",
                   [(e["date"], e["end"], e["year"], e["month"], e["day"], e["label"], e["category"], e["source"])
                    for e in events])
    ctx.note(f"events: {len(events)} from {ctx.decisions / 'events.yaml'}")
