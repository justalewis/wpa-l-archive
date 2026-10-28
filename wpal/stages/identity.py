"""Item 1: resolve posters into persons.

The unit of evidence is the poster key: the salted hash of one sending
address, which the parser computed and then threw the address away. Every
message from one address belongs to one person; that is the strongest merge
signal and needs no review.

Keys are then joined by name. Each key's names are normalized: "Last, First"
turned around, honorifics and degrees dropped, middle initials and
diacritics removed, case folded. Two keys whose most common normalized
names are identical (at least two tokens, a real first name) merge
automatically. Pairs that only share a surname and a first initial, or a
nickname pair (Bill/William), become review candidates and do not merge.

Human decisions, keyed on poster keys so no address or name list enters
git, come from:
  decisions/poster_merges.csv  poster_key_a,poster_key_b,note
  decisions/poster_splits.csv  poster_key,note   (keep this key out of name merges)

person_id is 10 hex from the hash of the cluster's earliest poster key, so
it is stable across rebuilds unless a decision moves that earliest key; a
moved id is recorded in person_redirects against the previous build.

Affiliations come from signature blocks (wpal/stages/fresh.py): lines with
institution words, normalized and counted per person, kept when they recur.
"""

from __future__ import annotations

import collections
import csv
import hashlib
import os
import re
import sqlite3
import unicodedata

PUBLIC_MIN_POSTS = int(os.environ.get("WPAL_PUBLIC_MIN_POSTS", 25))  # ethics threshold (PLAN.md decision 2)

DROP = re.compile(
    r"\b(dr|prof|professor|mr|mrs|ms|rev|ph\.?\s?d|ed\.?\s?d|m\.?f\.?a|m\.?a|jr|sr|ii|iii|phd|edd)\b\.?", re.I)
INSTITUTION = re.compile(
    r"((?:[A-Z][\w.&'-]*[ \t]+){0,5}(?:University|College|Institute|Polytechnic)(?:[ \t]+(?:of|at|for|in|and|the|[A-Z][\w.&'-]*)){0,6})")
# Signature lines that name a journal, a unit, or a mail service rather than a campus.
NOT_INSTITUTION = re.compile(
    r"^(College (English|Composition|Teaching|Literature)|University (Writing|Press|Station|Writing Program)|"
    r"Teaching English in the Two-Year College|Two-Year College|Community College Journal)|"
    r"\b(Press|Webmail|Station|Journal|Review|Quarterly|Conference|Council|Association)\b")
NICKNAMES = [{"william", "bill", "will", "billy"}, {"robert", "bob", "rob", "bobby"}, {"richard", "rich", "rick", "dick"},
             {"elizabeth", "liz", "beth", "betsy", "libby"}, {"margaret", "peggy", "meg", "maggie"},
             {"katherine", "kathryn", "kate", "kathy", "katie", "catherine", "cathy"}, {"michael", "mike"},
             {"james", "jim", "jimmy"}, {"edward", "ed", "ted", "ned"}, {"susan", "sue", "suzy"},
             {"joseph", "joe"}, {"thomas", "tom"}, {"daniel", "dan"}, {"david", "dave"}, {"christopher", "chris"},
             {"christine", "chris", "christy"}, {"jennifer", "jenny", "jen"}, {"patricia", "pat", "patty", "trish"},
             {"douglas", "doug"}, {"gregory", "greg"}, {"steven", "stephen", "steve"}, {"lawrence", "larry"},
             {"anthony", "tony"}, {"rebecca", "becky"}, {"deborah", "debbie", "deb"}, {"barbara", "barb"},
             {"nicholas", "nick"}, {"timothy", "tim"}, {"andrew", "andy", "drew"}, {"kenneth", "ken"},
             {"samuel", "sam"}, {"alexander", "alex"}, {"jonathan", "jon"}, {"donald", "don"}, {"ronald", "ron"}]
NICK = {n: i for i, group in enumerate(NICKNAMES) for n in group}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(raw: str) -> str:
    """'Bergmann, Linda' and 'Dr. Linda S. Bergmann, Ph.D.' both give 'linda bergmann'."""
    s = strip_accents(raw or "").strip().strip("'\"")
    if not s or s == "Unnamed poster" or "@" in s:
        return ""
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    if s.count(",") >= 1:
        left, right = [p.strip() for p in s.split(",", 1)]
        right_main = DROP.sub(" ", right).strip(" ,.")
        if 1 <= len(left.split()) <= 2 and 1 <= len(right_main.split()) <= 2 and right_main:
            s = f"{right_main} {left}"          # Last, First
        else:
            s = left                             # Name, Title or affiliation
    s = DROP.sub(" ", s)
    s = re.sub(r"[^A-Za-z' -]", " ", s).lower().replace("'", "")
    tokens = [t for t in s.split() if len(t) > 1 or t in ("a",)]  # drop middle initials
    tokens = [t for t in tokens if t not in ("a",)]
    if len(tokens) < 2:
        return " ".join(tokens)
    return f"{tokens[0]} {tokens[-1]}"


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if not (r.get(next(iter(r)), "") or "").startswith("#")]


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def run(db, ctx) -> None:
    from . import add_column

    # One unit per poster key; messages with no address are their own units by name.
    units: dict[str, dict] = {}
    for uid, key, name, nk, sent_at, sig, is_root, year in db.execute("""
            SELECT m.uid, m.poster_key, m.from_name, m.name_key, m.sent_at, m.signature,
                   m.uid = t.root_uid, m.year
            FROM messages m JOIN threads t ON t.id = m.thread_id"""):
        # Activity spans use the Date header only where it agrees with the log
        # month; a two-digit year can otherwise put a post in 1904.
        if sent_at and abs(int(sent_at[:4]) - year) > 1:
            sent_at = None
        unit = key or f"name:{nk}"
        u = units.setdefault(unit, {"names": collections.Counter(), "n": 0, "first": None, "last": None,
                                    "started": 0, "sigs": []})
        u["names"][name] += 1
        u["n"] += 1
        u["started"] += 1 if is_root else 0
        if sent_at:
            u["first"] = min(filter(None, [u["first"], sent_at]))
            u["last"] = max(filter(None, [u["last"], sent_at]))
        if sig:
            u["sigs"].append((sent_at, sig))

    for u in units.values():
        norms = collections.Counter()
        for name, n in u["names"].items():
            nn = normalize_name(name)
            if nn:
                norms[nn] += n
        u["norm"] = norms.most_common(1)[0][0] if norms else ""

    splits = {r["poster_key"].strip() for r in _read_csv(ctx.decisions / "poster_splits.csv")}
    merges = [(r["poster_key_a"].strip(), r["poster_key_b"].strip())
              for r in _read_csv(ctx.decisions / "poster_merges.csv")]

    dsu = DSU()
    by_norm = collections.defaultdict(list)
    for key, u in units.items():
        dsu.find(key)
        if u["norm"].count(" ") >= 1 and key not in splits:
            by_norm[u["norm"]].append(key)
    for keys in by_norm.values():
        for k in keys[1:]:
            dsu.union(keys[0], k)
    applied = 0
    for a, b in merges:
        if a in units and b in units:
            dsu.union(a, b)
            applied += 1

    clusters = collections.defaultdict(list)
    for key in units:
        clusters[dsu.find(key)].append(key)

    def earliest(keys):
        return min(keys, key=lambda k: (units[k]["first"] or "9999", k))

    person_of = {}
    persons = []
    aliases = []
    for keys in clusters.values():
        anchor = earliest(keys)
        pid = hashlib.sha256(anchor.encode()).hexdigest()[:10]
        names = collections.Counter()
        for k in keys:
            names.update(units[k]["names"])
            person_of[k] = pid
        n = sum(units[k]["n"] for k in keys)
        display = _display_name(names)
        firsts = [units[k]["first"] for k in keys if units[k]["first"]]
        lasts = [units[k]["last"] for k in keys if units[k]["last"]]
        started = sum(units[k]["started"] for k in keys)
        public = int(n >= PUBLIC_MIN_POSTS and display != "Unnamed poster")
        persons.append((pid, display, min(firsts) if firsts else None, max(lasts) if lasts else None,
                        n, started, n - started, len(keys), public))
        aliases += [(pid, name, normalize_name(name), c) for name, c in names.items()]

    db.executescript("""
        DROP TABLE IF EXISTS persons;
        DROP TABLE IF EXISTS person_aliases;
        DROP TABLE IF EXISTS person_keys;
        DROP TABLE IF EXISTS affiliations;
        DROP TABLE IF EXISTS merge_candidates;
        DROP TABLE IF EXISTS person_redirects;
        CREATE TABLE persons (
            person_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, first_at TEXT, last_at TEXT,
            n_posts INTEGER NOT NULL, n_started INTEGER NOT NULL, n_replies INTEGER NOT NULL,
            n_keys INTEGER NOT NULL, public INTEGER NOT NULL);
        CREATE TABLE person_aliases (person_id TEXT, name TEXT, norm TEXT, n INTEGER);
        CREATE INDEX person_aliases_p ON person_aliases(person_id);
        CREATE TABLE person_keys (person_id TEXT, poster_key TEXT PRIMARY KEY);
        CREATE TABLE affiliations (person_id TEXT, institution TEXT, first_at TEXT, last_at TEXT, n INTEGER);
        CREATE INDEX affiliations_p ON affiliations(person_id);
        CREATE TABLE merge_candidates (
            poster_key_a TEXT, poster_key_b TEXT, person_a TEXT, person_b TEXT,
            name_a TEXT, name_b TEXT, reason TEXT, confidence REAL);
        CREATE TABLE person_redirects (old_id TEXT PRIMARY KEY, new_id TEXT);
    """)
    db.executemany("INSERT INTO persons VALUES (?,?,?,?,?,?,?,?,?)", persons)
    db.executemany("INSERT INTO person_aliases VALUES (?,?,?,?)", aliases)
    db.executemany("INSERT INTO person_keys VALUES (?,?)",
                   [(pid, k) for k, pid in person_of.items() if not k.startswith("name:")])

    add_column(db, "messages", "person_id", "TEXT")
    db.execute("DROP TABLE IF EXISTS _unit_person")
    db.execute("CREATE TEMP TABLE _unit_person (unit TEXT PRIMARY KEY, person_id TEXT)")
    db.executemany("INSERT INTO _unit_person VALUES (?,?)", person_of.items())
    db.execute("""UPDATE messages SET person_id = (
                    SELECT person_id FROM _unit_person
                    WHERE unit = CASE WHEN messages.poster_key != '' THEN messages.poster_key
                                      ELSE 'name:' || messages.name_key END)""")
    db.execute("CREATE INDEX IF NOT EXISTS messages_person ON messages(person_id, sent_at)")

    _affiliations(db, units, person_of)
    _candidates(db, units, person_of, splits)
    # Name-based merges are automatic but reviewable: list every cluster that
    # joined more than one address by name, so a collision can be split.
    db.executemany("INSERT INTO merge_candidates VALUES (?,?,?,?,?,?,?,?)", [
        (keys[0], k, person_of[k], person_of[k], units[keys[0]]["names"].most_common(1)[0][0],
         units[k]["names"].most_common(1)[0][0], "merged: same normalized name", 0.9)
        for keys in clusters.values() if len(keys) > 1 for k in keys[1:]
        if not keys[0].startswith("name:") and not k.startswith("name:")])
    _redirects(db, ctx, person_of)

    n_public = sum(p[-1] for p in persons)
    ctx.note(f"identity: {len(units):,} poster keys -> {len(persons):,} persons "
             f"({n_public:,} public at {PUBLIC_MIN_POSTS}+ posts); {applied} merge decisions applied")


def _display_name(names: collections.Counter) -> str:
    """The most-used 'First Last' form; a 'Last, First' form is turned around.

    Messages keep the name as posted; this is only the person page's heading.
    """
    flipped = collections.Counter()
    for n, c in names.items():
        if n == "Unnamed poster":
            continue
        parts = [p.strip() for p in n.split(",")]
        if len(parts) == 2 and 1 <= len(parts[0].split()) <= 2 and 1 <= len(parts[1].split()) <= 3                 and not DROP.fullmatch(parts[1].strip(". ")):
            n = f"{parts[1]} {parts[0]}"
        flipped[n.strip()] += c
    if not flipped:
        return "Unnamed poster"
    return max(flipped.items(), key=lambda nc: (nc[1], -len(nc[0])))[0]


def _affiliations(db, units, person_of) -> None:
    seen = collections.defaultdict(lambda: {"n": 0, "first": None, "last": None})
    for key, u in units.items():
        pid = person_of[key]
        for sent_at, sig in u["sigs"]:
            for inst in {_clean_inst(m.group(0)) for m in INSTITUTION.finditer(sig)}:
                if not inst:
                    continue
                a = seen[(pid, inst)]
                a["n"] += 1
                if sent_at:
                    a["first"] = min(filter(None, [a["first"], sent_at]))
                    a["last"] = max(filter(None, [a["last"], sent_at]))
    db.executemany("INSERT INTO affiliations VALUES (?,?,?,?,?)",
                   [(pid, inst, a["first"], a["last"], a["n"]) for (pid, inst), a in seen.items() if a["n"] >= 2])


def _clean_inst(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" ,.;:-")
    s = re.sub(r"^(?:English|Education|Writing|Rhetoric|Composition|Department|Dept|Program|Center)\s+", "", s)
    if NOT_INSTITUTION.search(s):
        return ""
    s = re.sub(r"^(?:The|Department of [\w ]+? at|Dept\.? of [\w ]+? at)\s+", "", s)
    words = s.split()
    if len(words) < 2 or len(s) > 90:
        return ""
    return s


def _candidates(db, units, person_of, splits) -> None:
    """Likely-same pairs that were NOT merged: shared surname, compatible first names."""
    by_last = collections.defaultdict(list)
    for key, u in units.items():
        if u["norm"].count(" ") == 1:
            first, last = u["norm"].split()
            by_last[last].append((key, first))
    rows, seen = [], set()
    for last, group in by_last.items():
        if len(group) > 40:   # very common surnames: too many pairs to be useful
            continue
        for i, (ka, fa) in enumerate(group):
            for kb, fb in group[i + 1:]:
                pa, pb = person_of[ka], person_of[kb]
                if pa == pb or ka in splits or kb in splits or (min(pa, pb), max(pa, pb)) in seen:
                    continue
                if fa in NICK and NICK.get(fa) == NICK.get(fb):
                    reason, conf = "nickname", 0.7
                elif fa[0] == fb[0] and (fa.startswith(fb) or fb.startswith(fa)):
                    reason, conf = "first-name prefix", 0.6
                else:
                    continue
                seen.add((min(pa, pb), max(pa, pb)))
                rows.append((ka, kb, pa, pb, units[ka]["names"].most_common(1)[0][0],
                             units[kb]["names"].most_common(1)[0][0], reason, conf))
    db.executemany("INSERT INTO merge_candidates VALUES (?,?,?,?,?,?,?,?)", rows)


def _redirects(db, ctx, person_of) -> None:
    """Map last build's person ids to this build's, through shared poster keys."""
    prev = ctx.previous_db
    if not prev or not prev.exists():
        return
    try:
        old = sqlite3.connect(f"file:{prev.as_posix()}?mode=ro", uri=True)
        rows = old.execute("SELECT person_id, poster_key FROM person_keys").fetchall()
        old.close()
    except sqlite3.Error:
        return
    votes = collections.defaultdict(collections.Counter)
    for old_pid, key in rows:
        if key in person_of:
            votes[old_pid][person_of[key]] += 1
    current = set(person_of.values())
    db.executemany("INSERT OR REPLACE INTO person_redirects VALUES (?,?)",
                   [(o, c.most_common(1)[0][0]) for o, c in votes.items() if o not in current])
