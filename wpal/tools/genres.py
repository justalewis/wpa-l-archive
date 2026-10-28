"""Item 6 on the page: genre shares per year, the job-ad view, genre as a
search filter and export field, and genre marks on messages.

Shares count every message, withdrawn posters included (anonymous totals,
PLAN.md decision 1). Lists of job ads and anything showing text read the
`visible` view.
"""

from __future__ import annotations

from flask import abort, render_template, request

from ..app import NavItem
from ..stages.genre import LABELS

# Fixed categorical order: the eight named genres take --cat-1..8, "other" takes --cat-other.
ORDER = ["discussion", "debate", "query", "announcement", "cfp", "job", "tribute", "administrative", "other"]
NAMES = {"query": "Query", "discussion": "Discussion", "debate": "Debate", "cfp": "Call for papers", "job": "Job ad",
         "announcement": "Announcement", "tribute": "Tribute", "administrative": "List business", "other": "Other"}
ICONS = {"query": "ic-flashpoint", "discussion": "ic-thread", "debate": "ic-thread", "cfp": "ic-cfp", "job": "ic-job",
         "announcement": "ic-message", "tribute": "ic-tribute", "administrative": "ic-redpen", "other": "ic-genre"}


def register(app) -> None:
    app.nav.append(NavItem("genres", "Genres", "genres", "ic-genre", 40))

    def has_genre():
        return app.cached("has_genre", lambda: "genre" in {r[1] for r in app.con().execute("PRAGMA table_info(messages)")})

    @app.context_processor
    def _genre_ctx():
        return {"GENRE_NAMES": NAMES, "GENRE_ICONS": ICONS, "GENRE_ORDER": ORDER, "has_genre": has_genre()}

    @app.route("/genres")
    def genres():
        if not has_genre():
            abort(404)
        rows = app.cached("genre_year", lambda: [dict(r) for r in app.con().execute(
            "SELECT year, genre, COUNT(*) n FROM main.messages GROUP BY year, genre ORDER BY year")])
        years = {}
        for r in rows:
            years.setdefault(r["year"], {g: 0 for g in ORDER})[r["genre"] or "other"] += r["n"]
        totals = {y: sum(v.values()) for y, v in years.items()}
        shares = {y: {g: (v[g] / totals[y] if totals[y] else 0) for g in ORDER} for y, v in years.items()}
        evals = app.con().execute("SELECT * FROM genre_eval ORDER BY support DESC").fetchall()
        sources = app.cached("genre_sources", lambda: [dict(r) for r in app.con().execute(
            "SELECT CASE WHEN genre_source LIKE 'rule%' THEN 'rules' WHEN genre_source LIKE 'model%' THEN 'model' "
            "WHEN genre_source LIKE 'hand%' THEN 'hand' WHEN genre_source = 'structure' THEN 'thread structure' "
            "ELSE 'default' END src, COUNT(*) n FROM main.messages GROUP BY src ORDER BY n DESC")])
        return render_template("genres.html", years=years, shares=shares, totals=totals, evals=evals, sources=sources,
                               active_nav="genres", **_extras(app, "genres"))

    @app.route("/jobs")
    def jobs():
        if not has_genre():
            abort(404)
        by_year = app.cached("jobs_year", lambda: [dict(r) for r in app.con().execute(
            "SELECT year, rank_type, COUNT(*) n FROM jobs GROUP BY year, rank_type ORDER BY year")])
        kinds = ["tenure-line", "non-tenure-line", "WPA", "writing center director", "postdoc/fellowship", "other"]
        table = {}
        for r in by_year:
            table.setdefault(r["year"], {k: 0 for k in kinds})[r["rank_type"]] += r["n"]
        top = app.con().execute(
            "SELECT institution, COUNT(*) n, MIN(year) first, MAX(year) last FROM jobs WHERE institution != '' "
            "GROUP BY institution ORDER BY n DESC LIMIT 30").fetchall()
        rank = request.args.get("rank")
        year = request.args.get("year", type=int)
        listing = []
        if rank or year:
            listing = app.con().execute(
                "SELECT j.*, v.subject FROM jobs j JOIN visible v ON v.uid = j.uid WHERE (? IS NULL OR j.rank_type = ?) "
                "AND (? IS NULL OR j.year = ?) ORDER BY j.posted_at LIMIT 300", (rank, rank, year, year)).fetchall()
        peak = max((sum(v.values()) for v in table.values()), default=1)
        return render_template("jobs.html", table=table, kinds=kinds, top=top, listing=listing, rank=rank, year=year,
                               peak=peak, active_nav="genres")

    def message_genre(m):
        if not has_genre():
            return {}
        return {"genre": m["genre"]} if m["genre"] else {}

    app.hooks["message"].append(message_genre)


def _extras(app, name):
    out = {}
    for fn in app.hooks.setdefault(name, []):
        out.update(fn() or {})
    return out
