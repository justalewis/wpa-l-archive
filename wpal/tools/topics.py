"""Item 5 on the page: topic trajectories as small multiples, and a page per topic."""

from __future__ import annotations

import json

from flask import abort, render_template, request

from ..app import NavItem


def register(app) -> None:
    app.nav.append(NavItem("topics", "Topics", "topics", "ic-topics", 39))

    def ready():
        return app.cached("has_topics", lambda: bool(app.con().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'topics'").fetchone()) and bool(app.con().execute(
            "SELECT 1 FROM topics LIMIT 1").fetchone()))

    def yearly(key):
        rows = app.con().execute(
            "SELECT tm.year, SUM(tm.n) n FROM topic_months tm WHERE tm.topic_key = ? GROUP BY tm.year", (key,)).fetchall()
        totals = app.cached("threads_by_year_topics", lambda: dict(app.con().execute(
            "SELECT year, SUM(n) FROM (SELECT tm.year, tm.n FROM topic_months tm) GROUP BY year").fetchall()))
        return {r["year"]: (r["n"] / totals[r["year"]] if totals.get(r["year"]) else 0) for r in rows}

    @app.route("/topics")
    def topics():
        if not ready():
            abort(404)
        sort = request.args.get("sort", "size")
        order = {"size": "n_threads DESC", "peak": "peak_year", "label": "label COLLATE NOCASE"}.get(sort, "n_threads DESC")
        rows = app.con().execute(f"SELECT * FROM topics ORDER BY {order}").fetchall()
        series = {r["topic_key"]: yearly(r["topic_key"]) for r in rows}
        peak = max((max(s.values(), default=0) for s in series.values()), default=1) or 1
        run = {k: json.loads(v) for k, v in app.con().execute("SELECT key, value FROM topic_run")}
        return render_template("topics.html", rows=rows, series=series, peak=peak, run=run, sort=sort,
                               active_nav="topics")

    @app.route("/topic/<key>")
    def topic(key):
        if not ready():
            abort(404)
        t = app.con().execute("SELECT * FROM topics WHERE topic_key = ?", (key,)).fetchone()
        if not t:
            abort(404)
        terms = app.con().execute("SELECT term, weight FROM topic_terms WHERE topic_key = ? ORDER BY rank", (key,)).fetchall()
        threads = app.con().execute("""
            SELECT th.root_uid, th.subject, th.started_at, th.n_messages, tt.representative
            FROM topic_threads tt JOIN threads th ON th.root_uid = tt.thread_key
            WHERE tt.topic_key = ? AND EXISTS (SELECT 1 FROM visible v WHERE v.thread_id = th.id)
            ORDER BY tt.representative DESC, th.n_messages DESC LIMIT 40""", (key,)).fetchall()
        s = yearly(key)
        run = {k: json.loads(v) for k, v in app.con().execute("SELECT key, value FROM topic_run")}
        return render_template("topic.html", t=t, terms=terms, threads=threads, s=s, peak=max(s.values(), default=1) or 1,
                               run=run, active_nav="topics")
