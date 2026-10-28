"""The one search definition every surface shares.

The search page, saved corpora, exports and the API all turn the same
filter dictionary into the same WHERE clause over the `visible` view, so a
corpus saved from the page and the API call with the same parameters can
never disagree, and a suppressed poster can't leak through any of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FILTER_KEYS = ("q", "from", "start", "end", "genre", "fresh")


@dataclass
class Filters:
    q: str = ""
    who: str = ""
    start: int | None = None
    end: int | None = None
    genre: str = ""
    fresh: bool = False  # search only the poster's own words, not quoted text

    @classmethod
    def from_args(cls, args) -> "Filters":
        def year(v):
            try:
                y = int(v)
            except (TypeError, ValueError):
                return None
            return y if 1993 <= y <= 2021 else None

        return cls(
            q=(args.get("q") or "").strip()[:200],
            who=(args.get("from") or "").strip()[:80],
            start=year(args.get("start")),
            end=year(args.get("end")),
            genre=(args.get("genre") or "").strip()[:30],
            fresh=(args.get("fresh") or "") in ("1", "true", "on"),
        )

    def as_dict(self) -> dict:
        d = {"q": self.q, "from": self.who, "start": self.start, "end": self.end,
             "genre": self.genre, "fresh": "1" if self.fresh else ""}
        return {k: v for k, v in d.items() if v not in ("", None)}

    @property
    def empty(self) -> bool:
        return not (self.q or self.who or self.genre)

    @property
    def fts_table(self) -> str:
        return "fresh_fts" if self.fresh else "messages_fts"


def where(f: Filters, alias: str = "v") -> tuple[str, list]:
    """WHERE clause (without the keyword) and parameters for `visible {alias}`."""
    w, p = [], []
    if f.q:
        w.append(f"{alias}.id IN (SELECT rowid FROM {f.fts_table} WHERE {f.fts_table} MATCH ?)")
        p.append(fts_query(f.q))
    if f.who:
        w.append(f"{alias}.from_name LIKE ?")
        p.append(f"%{f.who}%")
    if f.start:
        w.append(f"{alias}.year >= ?")
        p.append(f.start)
    if f.end:
        w.append(f"{alias}.year <= ?")
        p.append(f.end)
    if f.genre:
        w.append(f"{alias}.genre = ?")
        p.append(f.genre)
    return (" AND ".join(w) or "1"), p


def fts_query(q: str) -> str:
    """Pass quoted phrases and bare words to FTS5; drop FTS syntax characters otherwise."""
    parts = re.findall(r'"[^"]+"|\S+', q)
    out = []
    for p in parts:
        if p.startswith('"') and p.endswith('"'):
            inner = re.sub(r'["]', "", p[1:-1])
            if inner.strip():
                out.append(f'"{inner}"')
        elif p.upper() in ("OR", "AND", "NOT"):
            out.append(p.upper())
        else:
            word = re.sub(r"[^\w*'-]", "", p).replace("'", "")
            if word:
                out.append(f'"{word.rstrip("*")}"' + ("*" if word.endswith("*") else ""))
    while out and out[-1] in ("OR", "AND", "NOT"):
        out.pop()
    while out and out[0] in ("OR", "AND", "NOT"):
        out.pop(0)
    return " ".join(out) or '""'
