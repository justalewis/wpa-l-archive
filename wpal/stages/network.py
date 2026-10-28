"""Item 7: who held the conversation.

Per era (five-year windows): a reply network between persons, built from the
edges table (wpal/stages/confidence.py) so each edge carries its link
methods and pre-2010 inferred edges can be left out. Degree, betweenness
and k-core number per person; a laid-out graph of the most connected.

Per year: concentration of posting (share of messages from the top 1% and
top 10% of posters, and the Gini coefficient). Per cohort (year of first
post): the share still posting 1, 3 and 5 years later. Per era: thread
starters versus repliers.

Everyone is counted here, withdrawn posters included, because these are
aggregates; the pages apply the ethics thresholds (25 posts, 3 interactions)
and drop withdrawn people from anything that names a person.
"""

from __future__ import annotations

import collections
import json

ERAS = [(1993, 1997), (1998, 2002), (2003, 2007), (2008, 2012), (2013, 2017), (2018, 2021)]
STRONG_METHODS = ("message-id", "override", "wrote-line")
GRAPH_NODES = 60
SEED = 1993


def era_of(year: int) -> str:
    for a, b in ERAS:
        if a <= year <= b:
            return f"{a}–{b}"
    return ""


def gini(xs: list[int]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if not n or not sum(xs):
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * sum(xs)) - (n + 1) / n


def run(db, ctx) -> None:
    import networkx as nx

    db.executescript("""
        DROP TABLE IF EXISTS person_edges;
        DROP TABLE IF EXISTS person_era_stats;
        DROP TABLE IF EXISTS era_graphs;
        DROP TABLE IF EXISTS concentration;
        DROP TABLE IF EXISTS cohorts;
        DROP TABLE IF EXISTS era_roles;
        CREATE TABLE person_edges (era TEXT, src TEXT, dst TEXT, n INTEGER, n_strong INTEGER);
        CREATE INDEX person_edges_era ON person_edges(era);
        CREATE TABLE person_era_stats (era TEXT, person_id TEXT, posts INTEGER, started INTEGER, degree INTEGER,
                                       strength INTEGER, betweenness REAL, core INTEGER, PRIMARY KEY (era, person_id));
        CREATE TABLE era_graphs (era TEXT, scope TEXT, layout TEXT, PRIMARY KEY (era, scope));
        CREATE TABLE concentration (year INTEGER PRIMARY KEY, posters INTEGER, top1 REAL, top10 REAL, gini REAL);
        CREATE TABLE cohorts (first_year INTEGER, after_years INTEGER, cohort_size INTEGER, retained REAL,
                              PRIMARY KEY (first_year, after_years));
        CREATE TABLE era_roles (era TEXT PRIMARY KEY, posters INTEGER, starters_only REAL, repliers_only REAL, both REAL);
    """)

    # Edges between different persons, per era, with how many were strongly linked.
    agg = collections.defaultdict(lambda: [0, 0])
    for year, src, dst, method in db.execute(
            "SELECT year, child_person, parent_person, link_method FROM edges "
            "WHERE child_person IS NOT NULL AND parent_person IS NOT NULL AND child_person != parent_person"):
        e = agg[(era_of(year), src, dst)]
        e[0] += 1
        e[1] += method in STRONG_METHODS
    db.executemany("INSERT INTO person_edges VALUES (?,?,?,?,?)", [(*k, *v) for k, v in agg.items()])

    posts = collections.defaultdict(collections.Counter)
    started = collections.defaultdict(collections.Counter)
    by_year = collections.defaultdict(collections.Counter)
    first_year, years_active = {}, collections.defaultdict(set)
    for year, pid, is_root in db.execute(
            "SELECT m.year, m.person_id, m.uid = t.root_uid FROM messages m JOIN threads t ON t.id = m.thread_id "
            "WHERE m.person_id IS NOT NULL"):
        era = era_of(year)
        posts[era][pid] += 1
        started[era][pid] += bool(is_root)
        by_year[year][pid] += 1
        first_year[pid] = min(first_year.get(pid, year), year)
        years_active[pid].add(year)

    stats = []
    for a, b in ERAS:
        era = f"{a}–{b}"
        for scope, methods in (("all", None), ("strong", STRONG_METHODS)):
            g = nx.Graph()
            for (e, s, d), (n, n_strong) in agg.items():
                if e != era:
                    continue
                w = n if scope == "all" else n_strong
                if w <= 0:
                    continue
                if g.has_edge(s, d):
                    g[s][d]["weight"] += w
                else:
                    g.add_edge(s, d, weight=w)
            if scope == "all":
                core = nx.core_number(g) if g.number_of_nodes() else {}
                # Betweenness on the strongest ties (3+ exchanges), sampled for speed, seeded.
                h = nx.Graph([(u, v, d) for u, v, d in g.edges(data=True) if d["weight"] >= 3])
                k = min(400, h.number_of_nodes())
                btw = nx.betweenness_centrality(h, k=k, seed=SEED, normalized=True) if k > 2 else {}
                for pid, n in posts[era].items():
                    stats.append((era, pid, n, started[era][pid], g.degree(pid) if pid in g else 0,
                                  int(g.degree(pid, weight="weight")) if pid in g else 0,
                                  round(btw.get(pid, 0.0), 6), core.get(pid, 0)))
            db.execute("INSERT INTO era_graphs VALUES (?,?,?)", (era, scope, json.dumps(_layout(g, nx))))
    db.executemany("INSERT INTO person_era_stats VALUES (?,?,?,?,?,?,?,?)", stats)

    conc = []
    for year, c in sorted(by_year.items()):
        counts = sorted(c.values(), reverse=True)
        total = sum(counts)
        n = len(counts)
        top1 = sum(counts[:max(1, round(n * 0.01))]) / total
        top10 = sum(counts[:max(1, round(n * 0.10))]) / total
        conc.append((year, n, round(top1, 4), round(top10, 4), round(gini(counts), 4)))
    db.executemany("INSERT INTO concentration VALUES (?,?,?,?,?)", conc)

    cohort = collections.defaultdict(list)
    for pid, fy in first_year.items():
        cohort[fy].append(pid)
    rows = []
    for fy, members in sorted(cohort.items()):
        for k in (1, 3, 5):
            if fy + k > 2020:  # 2021 is a partial year; don't score retention into it
                continue
            kept = sum(1 for p in members if any(y >= fy + k for y in years_active[p]))
            rows.append((fy, k, len(members), round(kept / len(members), 4)))
    db.executemany("INSERT INTO cohorts VALUES (?,?,?,?)", rows)

    roles = []
    for a, b in ERAS:
        era = f"{a}–{b}"
        ps = posts[era]
        if not ps:
            continue
        s_only = sum(1 for p in ps if started[era][p] == ps[p])
        r_only = sum(1 for p in ps if started[era][p] == 0)
        both = len(ps) - s_only - r_only
        roles.append((era, len(ps), s_only / len(ps), r_only / len(ps), both / len(ps)))
    db.executemany("INSERT INTO era_roles VALUES (?,?,?,?,?)", roles)
    ctx.note(f"network: {len(agg):,} person-to-person edges across {len(ERAS)} eras")


def _layout(g, nx) -> dict:
    """The GRAPH_NODES most connected people, laid out (seeded spring layout)."""
    if g.number_of_nodes() == 0:
        return {"nodes": [], "edges": []}
    top = sorted(g.degree(weight="weight"), key=lambda kv: -kv[1])[:GRAPH_NODES]
    keep = {p for p, _ in top}
    sub = g.subgraph(keep).copy()
    pos = nx.spring_layout(sub, seed=SEED, weight="weight", k=1.2 / max(1, len(keep)) ** 0.5, iterations=200)
    return {"nodes": [{"id": p, "x": round(float(pos[p][0]), 4), "y": round(float(pos[p][1]), 4), "w": int(w)}
                      for p, w in top],
            "edges": [{"a": u, "b": v, "w": int(d["weight"])} for u, v, d in sub.edges(data=True)]}
