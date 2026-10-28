"""Item 10: which institutions the list represented, over time.

Institution names come from posters' signature affiliations (identity stage)
and from job ads (genre stage). Each name is matched to an IPEDS unit:

  1. decisions/institution_matches.csv (name,unitid; unitid "none" rejects);
  2. exact match on a normalized name or IPEDS alias;
  3. a fuzzy match only when exactly one IPEDS institution contains every
     word of the name (RapidFuzz token-set ratio 100) and no exact match
     exists. Ambiguous names ("University of California") stay unmatched.

Unmatched names are kept, with counts, for review (wpal.review institutions).
IPEDS lists institutions open in 2022, so closed or merged schools appear as
unmatched rather than silently dropped.

A post is attributed to an institution when its poster had that affiliation
within the span of their signatures that name it; posts with no affiliation
count as unknown. Carnegie types are the 2021 basic classification, grouped.

Reference data: data/reference/ipeds/hd2022.csv (NCES IPEDS directory, 2022).
"""

from __future__ import annotations

import collections
import csv
import re

from .network import ERAS

CARNEGIE = [  # (lowest code, highest code, group)
    (1, 14, "Associate's (two-year)"), (15, 15, "Doctoral: very high research"), (16, 16, "Doctoral: high research"),
    (17, 17, "Doctoral/professional"), (18, 20, "Master's"), (21, 23, "Baccalaureate"), (24, 32, "Special focus"),
    (33, 33, "Tribal"),
]
TYPE_ORDER = [g for _, _, g in CARNEGIE] + ["Not classified"]


def carnegie_type(code: int) -> str:
    for lo, hi, g in CARNEGIE:
        if lo <= code <= hi:
            return g
    return "Not classified"


def norm(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\buniv\b\.?", "university", s)
    s = re.sub(r"\bst\.\s", "saint ", s)
    s = re.sub(r"\bcuny\b", "city university of new york", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|at)\b", " ", s)
    return " ".join(s.split())


def run(db, ctx) -> None:
    path = ctx.reference / "ipeds" / "hd2022.csv"
    db.executescript("""
        DROP TABLE IF EXISTS institutions;
        DROP TABLE IF EXISTS institution_aliases;
        DROP TABLE IF EXISTS person_institutions;
        DROP TABLE IF EXISTS unmatched_institutions;
        DROP TABLE IF EXISTS post_carnegie;
        DROP TABLE IF EXISTS institution_era;
        CREATE TABLE institutions (unitid INTEGER PRIMARY KEY, name TEXT, city TEXT, state TEXT, carnegie_code INTEGER,
                                   carnegie_type TEXT, lat REAL, lon REAL);
        CREATE TABLE institution_aliases (name TEXT PRIMARY KEY, unitid INTEGER, method TEXT, confidence REAL);
        CREATE TABLE person_institutions (person_id TEXT, unitid INTEGER, first_at TEXT, last_at TEXT, n INTEGER);
        CREATE INDEX person_institutions_u ON person_institutions(unitid);
        CREATE TABLE unmatched_institutions (name TEXT PRIMARY KEY, n INTEGER, sources TEXT);
        CREATE TABLE post_carnegie (year INTEGER, carnegie_type TEXT, posts INTEGER, PRIMARY KEY (year, carnegie_type));
        CREATE TABLE institution_era (era TEXT, unitid INTEGER, posts INTEGER, people INTEGER, PRIMARY KEY (era, unitid));
    """)
    if not path.exists():
        ctx.note(f"institutions: skipped ({path} not found)")
        return
    from rapidfuzz import fuzz, process

    ipeds, exact = {}, {}
    with open(path, encoding="latin-1", newline="") as fh:
        for r in csv.DictReader(fh):
            uid = int(r["UNITID"])
            code = int(r["C21BASIC"]) if r["C21BASIC"].lstrip("-").isdigit() else -2
            ipeds[uid] = (r["INSTNM"], r["CITY"], r["STABBR"], code, carnegie_type(code),
                          float(r["LATITUDE"] or 0), float(r["LONGITUD"] or 0))
            for nm in [r["INSTNM"], *re.split(r"[|;,]", r["IALIAS"] or "")]:
                k = norm(nm)
                if len(k) > 4:
                    exact.setdefault(k, set()).add(uid)
    names = {k: k for k in exact}

    mentions = collections.Counter()
    sources = collections.defaultdict(set)
    for inst, n in db.execute("SELECT institution, SUM(n) FROM affiliations GROUP BY institution"):
        mentions[inst] += n
        sources[inst].add("signatures")
    try:
        for inst, n in db.execute("SELECT institution, COUNT(*) FROM jobs WHERE institution != '' GROUP BY institution"):
            mentions[inst] += n
            sources[inst].add("job ads")
    except Exception:
        pass

    decided = {}
    dpath = ctx.decisions / "institution_matches.csv"
    if dpath.exists():
        with open(dpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                decided[r["name"].strip()] = None if r["unitid"].strip() == "none" else int(r["unitid"])

    alias_rows, unmatched = [], []
    match = {}
    for raw, n in mentions.items():
        if raw in decided:
            if decided[raw]:
                match[raw] = decided[raw]
                alias_rows.append((raw, decided[raw], "decision", 1.0))
            continue
        k = norm(raw)
        hits = exact.get(k, set())
        if len(hits) == 1:
            match[raw] = next(iter(hits))
            alias_rows.append((raw, match[raw], "exact", 1.0))
            continue
        cands = process.extract(k, names.keys(), scorer=fuzz.token_set_ratio, limit=6, score_cutoff=100)
        uids = {u for c, _, _ in cands for u in exact[c]}
        if len(uids) == 1 and len(k.split()) >= 2:
            match[raw] = next(iter(uids))
            alias_rows.append((raw, match[raw], "fuzzy", 0.85))
        else:
            unmatched.append((raw, n, ", ".join(sorted(sources[raw]))))

    used = set(match.values())
    db.executemany("INSERT INTO institutions VALUES (?,?,?,?,?,?,?,?)",
                   [(u, *ipeds[u]) for u in used if u in ipeds])
    db.executemany("INSERT INTO institution_aliases VALUES (?,?,?,?)", alias_rows)
    db.executemany("INSERT INTO unmatched_institutions VALUES (?,?,?)", unmatched)

    spans = collections.defaultdict(list)
    for pid, inst, first, last, n in db.execute("SELECT person_id, institution, first_at, last_at, n FROM affiliations"):
        if inst in match:
            spans[pid].append((match[inst], first or "", last or "9999", n))
    pi = collections.defaultdict(lambda: [None, None, 0])
    for pid, rows in spans.items():
        for u, f, l, n in rows:
            r = pi[(pid, u)]
            r[0] = min(filter(None, [r[0], f])) if f else r[0]
            r[1] = max(filter(None, [r[1], l])) if l else r[1]
            r[2] += n
    db.executemany("INSERT INTO person_institutions VALUES (?,?,?,?,?)", [(p, u, *v) for (p, u), v in pi.items()])

    ctype = {u: ipeds[u][4] for u in used if u in ipeds}
    counts = collections.Counter()
    era_posts, era_people = collections.Counter(), collections.defaultdict(set)
    for year, pid, sent in db.execute("SELECT year, person_id, sent_at FROM messages"):
        best = None
        for u, f, l, n in spans.get(pid, ()):
            if sent and f[:10] <= sent[:10] <= l[:10] and (best is None or n > best[1]):
                best = (u, n)
        counts[(year, ctype.get(best[0], "Not classified") if best else "Unknown")] += 1
        era = next((f"{a}–{b}" for a, b in ERAS if year and a <= year <= b), None)
        if best and era:  # the page's per-era ranking, precomputed: the join is too slow per request
            era_posts[(era, best[0])] += 1
            era_people[(era, best[0])].add(pid)
    db.executemany("INSERT INTO post_carnegie VALUES (?,?,?)", [(y, t, n) for (y, t), n in counts.items()])
    db.executemany("INSERT INTO institution_era VALUES (?,?,?,?)",
                   [(e, u, n, len(era_people[(e, u)])) for (e, u), n in era_posts.items()])
    ctx.note(f"institutions: {len(match):,} names matched to {len(used):,} IPEDS units; {len(unmatched):,} unmatched")
