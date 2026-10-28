"""Item 4: normalized term trajectories.

/trends?terms=assessment,outcomes,portfolio

For each term: messages containing it per 1,000 messages, or occurrences per
10,000 words, by month or by year, against the denominators the build
precomputes (wpal/stages/trends.py). Raw counts are a toggle. "Own words
only" searches the bodies with quoted text and signatures removed, so a term
repeated down a chain of quoting replies counts once. Counts include
withdrawn posters' messages: they are anonymous totals (PLAN.md decision 1).

"Messages containing" uses the search index, with its stemming: "assess"
matches "assessment". "Occurrences" counts the word or phrase itself, with up
to three trailing letters for plurals and inflections, so the two measures can
differ slightly for a stemmed term.
"""

from __future__ import annotations

import collections
import re
from urllib.parse import urlencode

from flask import render_template, request, url_for

from ..app import NavItem
from ..query import fts_query

MAX_TERMS = 6
SMOOTHING = {"0": 1, "3": 3, "12": 12}
OCCURRENCE_CAP = 40_000  # beyond this many matching messages, occurrences are not counted


def months():
    out = [(y, m) for y in range(1993, 2022) for m in range(1, 13)]
    return [ym for ym in out if ym <= (2021, 5)]


def register(app) -> None:
    app.nav.append(NavItem("trends", "Trends", "trends", "ic-trends", 35))
    cache: "collections.OrderedDict" = collections.OrderedDict()

    def denominators():
        def load():
            try:
                return {(r[0], r[1]): dict(r) for r in app.con().execute("SELECT * FROM month_totals")}
            except Exception:
                return {}
        return app.cached("month_totals", load)

    def counts(term: str, fresh: bool, occurrences: bool) -> dict:
        key = (term, fresh, occurrences)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        fts = "fresh_fts" if fresh else "messages_fts"
        col = "body_fresh" if fresh else "body"
        q = fts_query(term)
        out = collections.Counter()
        if not occurrences:
            for y, m, n in app.con().execute(
                    f"SELECT year, month, COUNT(*) FROM main.messages WHERE id IN "
                    f"(SELECT rowid FROM {fts} WHERE {fts} MATCH ?) GROUP BY year, month", (q,)):
                out[(y, m)] = n
        else:
            n = app.con().execute(f"SELECT COUNT(*) FROM {fts} WHERE {fts} MATCH ?", (q,)).fetchone()[0]
            if n > OCCURRENCE_CAP:
                out = None
            else:
                words = [re.escape(w) for w in re.findall(r"[\w'-]+", term.replace('"', ""))]
                rx = re.compile(r"\b" + r"\W+".join(words) + r"\w{0,3}\b", re.I) if words else None
                for y, m, text, subj in app.con().execute(
                        f"SELECT year, month, {col}, subject FROM main.messages WHERE id IN "
                        f"(SELECT rowid FROM {fts} WHERE {fts} MATCH ?)", (q,)):
                    out[(y, m)] += len(rx.findall(text or "")) + len(rx.findall(subj or "")) if rx else 0
        cache[key] = out
        if len(cache) > 64:
            cache.popitem(last=False)
        return out

    app.trend_counts = counts
    app.trend_denominators = denominators

    @app.route("/trends")
    def trends():
        raw_terms = request.args.get("terms", "")
        terms = [t.strip() for t in re.split(r"[,\n]", raw_terms) if t.strip()][:MAX_TERMS]
        unit = request.args.get("unit", "year") if request.args.get("unit") in ("month", "year") else "year"
        measure = "raw" if request.args.get("measure") == "raw" else "rate"
        mode = "occurrences" if request.args.get("count") == "occurrences" else "messages"
        fresh = request.args.get("fresh") == "1"
        smooth = request.args.get("smooth", "0") if request.args.get("smooth") in SMOOTHING else "0"
        den = denominators()
        axis = months() if unit == "month" else [(y, 0) for y in range(1993, 2022)]

        def denom(y, m):
            rows = [den[k] for k in den if k[0] == y and (m == 0 or k[1] == m)]
            if mode == "messages":
                return sum(r["messages"] for r in rows)
            return sum(r["own_words" if fresh else "words"] for r in rows)

        dens = {p: denom(*p) for p in axis}
        series, notes = [], []
        for i, term in enumerate(terms):
            c = counts(term, fresh, mode == "occurrences")
            if c is None:
                notes.append(f"“{term}” matches more than {OCCURRENCE_CAP:,} messages; "
                             "occurrences are not counted for it. Use “messages containing.”")
                continue
            vals = []
            for (y, m) in axis:
                n = sum(v for (yy, mm), v in c.items() if yy == y and (m == 0 or mm == m))
                d = dens[(y, m)]
                scale = 1000 if mode == "messages" else 10000
                vals.append(n if measure == "raw" else (n * scale / d if d else 0.0))
            k = SMOOTHING[smooth] if unit == "month" else 1
            if k > 1:
                vals = [sum(vals[max(0, j - k + 1):j + 1]) / len(vals[max(0, j - k + 1):j + 1]) for j in range(len(vals))]
            total = sum(c.values())
            series.append({"term": term, "slot": i + 1, "vals": vals, "total": total})

        chart = layout(series, axis, unit) if series else None
        event_marks = []
        if chart and hasattr(app, "event_overlay"):
            idx = {p: i for i, p in enumerate(axis)}
            xs = [pt[0] for pt in series[0]["points"]]
            event_marks = app.event_overlay(
                lambda y, m: xs[idx[(y, m if unit == "month" else 0)]] if (y, m if unit == "month" else 0) in idx else None)
        params = {"terms": ", ".join(terms), "unit": unit, "measure": measure, "count": mode,
                  "fresh": "1" if fresh else "", "smooth": smooth}

        def with_(**kw):
            return url_for("trends") + "?" + urlencode({k: v for k, v in {**params, **kw}.items() if v})

        def point_url(term, y, m):
            q = {"q": term, "start": y, "end": y}
            if m:
                q["month"] = m
            if fresh:
                q["fresh"] = "1"
            return url_for("search") + "?" + urlencode(q)

        return render_template("trends.html", terms=terms, raw_terms=raw_terms, unit=unit, measure=measure, mode=mode,
                               fresh=fresh, smooth=smooth, chart=chart, series=series, notes=notes, with_=with_,
                               point_url=point_url, has_denominators=bool(den), max_terms=MAX_TERMS,
                               event_marks=event_marks,
                               active_nav="trends", **app_extras("trends"))

    def app_extras(name):
        out = {}
        for fn in app.hooks.setdefault(name, []):
            out.update(fn() or {})
        return out

    # The search page's histogram gets a per-1,000-messages view.
    def search_norm(f, by_year):
        if not by_year:
            return {}
        den = denominators()
        per_year = collections.Counter()
        for (y, _m), r in den.items():
            per_year[y] += r["messages"]
        return {"by_year_rate": {r["year"]: (r["n"] * 1000 / per_year[r["year"]] if per_year[r["year"]] else 0)
                                 for r in by_year},
                "trends_link": url_for("trends", terms=f.q) if f.q else None}

    app.hooks["search"].append(search_norm)


def layout(series, axis, unit):
    """Geometry for a server-drawn line chart (one axis, up to six series)."""
    W, H, X0, Y0, top = 1000, 300, 56, 260, 16
    peak = max((max(s["vals"]) for s in series), default=0) or 1
    n = len(axis)
    step = (W - X0 - 10) / max(n - 1, 1)

    def x(i):
        return X0 + i * step

    def y(v):
        return Y0 - (v / peak) * (Y0 - top)

    for s in series:
        s["points"] = [(round(x(i), 1), round(y(v), 1), v, axis[i]) for i, v in enumerate(s["vals"])]
        s["path"] = " ".join(f"{px},{py}" for px, py, _, _ in s["points"])
    ticks = [peak / 2, peak]
    years = [(round(x(axis.index((yr, 1 if unit == "month" else 0))), 1), yr)
             for yr in range(1993, 2022, 4) if (yr, 1 if unit == "month" else 0) in axis]
    return {"W": W, "H": H, "X0": X0, "Y0": Y0, "ticks": [(round(y(t), 1), t) for t in ticks], "years": years,
            "dense": unit == "month"}
