"""Item 9: works and scholars the list talked about, linked to Pinakes.

Reads the poster's own words (quoted text excluded, so a reference repeated
down a chain of replies counts once) and finds:

  doi         a DOI, matched exactly to a Pinakes work            (accepted)
  title       a quoted or italicized title of 4+ words, matched
              exactly (normalized) to a Pinakes title or main title
              before its colon; unique match                       (accepted)
              shared by several works                              (review)
  author-year "Surname (2004)", "(Surname, 2004)", "Surname 2004";
              matched when exactly one Pinakes work has an author
              with that surname in that year                       (review)

Scholar names (a Pinakes author with 3+ works, mentioned by full name in a
message they did not write) are counted separately, without a work.

decisions/citation_matches.csv (uid,raw,work_id,decision) accepts or
rejects individual matches and survives rebuilds.

Reference data: data/reference/pinakes/works.jsonl (tools/pinakes_dump.py).
"""

from __future__ import annotations

import collections
import csv
import json
import re
import unicodedata

DOI = re.compile(r"\b10\.\d{4,9}/[^\s\"<>,;]+", re.I)
QUOTED = re.compile(r"[\"“]([^\"”\n]{12,200})[\"”]|(?<![\w*])[_*]([^_*\n]{12,200})[_*](?![\w*])")
AUTHOR_YEAR = re.compile(r"\b([A-Z][a-zA-Z'\-]{2,})(?:,? et al\.?)?(?: and [A-Z][a-zA-Z'\-]+)?,? \(?((?:19[6-9]|20[0-2])\d)\)?")
FULL_NAME = re.compile(r"\b([A-Z][a-z]+(?:-[A-Z][a-z]+)?)(?: [A-Z]\.)? ([A-Z][a-zA-Z'\-]+(?:-[A-Z][a-z]+)?)\b")
STOP_SURNAMES = {"The", "This", "That", "From", "Fall", "Spring", "Summer", "Winter", "January", "February", "March",
                 "April", "May", "June", "July", "August", "September", "October", "November", "December", "In",
                 "Since", "Until", "Before", "After", "During", "CCCC", "MLA", "NCTE", "WPA", "Cs", "Vol", "Volume"}


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(re.findall(r"[a-z0-9]+", t))


def run(db, ctx) -> None:
    db.executescript("""
        DROP TABLE IF EXISTS mentions;
        DROP TABLE IF EXISTS cited_works;
        DROP TABLE IF EXISTS scholar_mentions;
        DROP TABLE IF EXISTS cited_scholars;
        CREATE TABLE mentions (id INTEGER PRIMARY KEY, uid TEXT, year INTEGER, kind TEXT, raw TEXT, work_id INTEGER,
                               work_title TEXT, confidence REAL, status TEXT);
        CREATE INDEX mentions_uid ON mentions(uid);
        CREATE INDEX mentions_work ON mentions(work_id, status);
        CREATE TABLE cited_works (work_id INTEGER PRIMARY KEY, title TEXT, authors TEXT, year INTEGER, journal TEXT,
                                  doi TEXT, n_mentions INTEGER, n_messages INTEGER, first_year INTEGER,
                                  pinakes_cites INTEGER);
        CREATE TABLE scholar_mentions (uid TEXT, year INTEGER, name TEXT);
        CREATE INDEX scholar_mentions_name ON scholar_mentions(name);
        CREATE TABLE cited_scholars (name TEXT PRIMARY KEY, n INTEGER, n_messages INTEGER, first_year INTEGER);
    """)
    path = ctx.reference / "pinakes" / "works.jsonl"
    if not path.exists():
        ctx.note(f"citations: skipped ({path} not found; run tools/pinakes_dump.py)")
        return
    works, by_doi, by_title, by_main, by_author_year = {}, {}, collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    author_works = collections.Counter()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            w = json.loads(line)
            wid = w["id"]
            year = int(w["pub_date"][:4]) if w.get("pub_date") and w["pub_date"][:4].isdigit() else None
            works[wid] = (w["title"], w.get("authors") or "", year, w.get("journal") or "", w.get("doi") or "",
                          w.get("internal_cited_by_count") or 0)
            if w.get("doi"):
                by_doi[w["doi"].lower()] = wid
            nt = norm_title(w["title"])
            if len(nt.split()) >= 4:
                by_title[nt].add(wid)
            main = norm_title(re.split(r"[:?]", w["title"] or "")[0])
            if len(main.split()) >= 4:
                by_main[main].add(wid)
            for a in (w.get("authors") or "").split(";"):
                a = a.strip()
                if not a:
                    continue
                author_works[a] += 1
                if year:
                    by_author_year[(a.split()[-1].lower(), year)].add(wid)
    scholars = {}
    for a, n in author_works.items():
        parts = a.replace(".", " ").split()
        if n >= 3 and len(parts) >= 2:
            scholars[(parts[0].lower(), parts[-1].lower())] = a

    decided = {}
    dpath = ctx.decisions / "citation_matches.csv"
    if dpath.exists():
        with open(dpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                decided[(r["uid"], r["raw"])] = (int(r["work_id"]) if r.get("work_id") else None, r["decision"])

    mentions, scholar_rows = [], []
    has_fresh = "body_fresh" in {r[1] for r in db.execute("PRAGMA table_info(messages)")}
    for uid, year, text, author in db.execute(
            f"SELECT uid, year, {'body_fresh' if has_fresh else 'body'}, from_name FROM messages"):
        if not text:
            continue
        found = set()
        for m in DOI.finditer(text):
            doi = m.group(0).rstrip(".)]").lower()
            if doi in by_doi:
                found.add(("doi", m.group(0), by_doi[doi], 1.0))
        for m in QUOTED.finditer(text):
            raw = (m.group(1) or m.group(2)).strip()
            nt = norm_title(raw)
            if len(nt.split()) < 4:
                continue
            hits = by_title.get(nt) or by_main.get(nt) or by_main.get(norm_title(re.split(r"[:?]", raw)[0]))
            if hits:
                wid = min(hits)
                found.add(("title", raw, wid, 0.95 if len(hits) == 1 else 0.6))
        for m in AUTHOR_YEAR.finditer(text):
            surname, yr = m.group(1), int(m.group(2))
            if surname in STOP_SURNAMES:
                continue
            hits = by_author_year.get((surname.lower(), yr))
            if hits and len(hits) == 1:
                found.add(("author-year", m.group(0), next(iter(hits)), 0.7))
        own = author.lower().replace(",", " ").split()
        seen_names = set()
        for m in FULL_NAME.finditer(text):
            key = (m.group(1).lower(), m.group(2).lower())
            if key in scholars and key[1] not in own and scholars[key] not in seen_names:
                seen_names.add(scholars[key])
                scholar_rows.append((uid, year, scholars[key]))
        for kind, raw, wid, conf in found:
            status = "accepted" if conf >= 0.9 else "review"
            dec = decided.get((uid, raw))
            if dec:
                status = "accepted" if dec[1] == "accept" else "rejected"
                wid = dec[0] or wid
            mentions.append((uid, year, kind, raw[:300], wid, works[wid][0], conf, status))

    db.executemany("INSERT INTO mentions (uid, year, kind, raw, work_id, work_title, confidence, status) "
                   "VALUES (?,?,?,?,?,?,?,?)", mentions)
    db.executemany("INSERT INTO scholar_mentions VALUES (?,?,?)", scholar_rows)
    agg = collections.defaultdict(lambda: [0, set(), 9999])
    for uid, year, kind, raw, wid, title, conf, status in mentions:
        if status == "accepted":
            a = agg[wid]
            a[0] += 1
            a[1].add(uid)
            a[2] = min(a[2], year)
    db.executemany("INSERT INTO cited_works VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(wid, *works[wid][:5], a[0], len(a[1]), a[2], works[wid][5]) for wid, a in agg.items()])
    sch = collections.defaultdict(lambda: [0, set(), 9999])
    for uid, year, name in scholar_rows:
        s = sch[name]
        s[0] += 1
        s[1].add(uid)
        s[2] = min(s[2], year)
    db.executemany("INSERT INTO cited_scholars VALUES (?,?,?,?)", [(n, s[0], len(s[1]), s[2]) for n, s in sch.items()])
    kinds = collections.Counter((m[2], m[7]) for m in mentions)
    ctx.note("citations: " + ", ".join(f"{k}/{s} {n:,}" for (k, s), n in sorted(kinds.items()))
             + f"; {len(scholar_rows):,} scholar-name mentions")


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None
