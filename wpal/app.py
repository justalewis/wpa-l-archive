"""The WPA-L Archive Reader: a small Flask app over data/archive.db.

    flask --app wpal.app run --port 5057

Every page is noindex (robots meta, X-Robots-Tag, robots.txt). Every query
reads the `visible` view, so a suppression applies to browse, threads,
permalinks, search, and the timeline at once.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import secrets
from datetime import datetime

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape

from . import db as dbmod

PAGE = 50
MONTHS = list(calendar.month_name)


def create_app(archive: str | None = None, requests_db: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ.get("WPAL_SECRET") or secrets.token_hex(32)
    app.config["ARCHIVE"] = archive or dbmod.archive_path()
    app.config["REQUESTS"] = requests_db or dbmod.requests_path()
    dbmod.init_requests(app.config["REQUESTS"])

    def con():
        if "con" not in g:
            g.con = dbmod.connect(app.config["ARCHIVE"], app.config["REQUESTS"])
        return g.con

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("con", None)
        if c is not None:
            c.close()

    @app.after_request
    def _headers(resp):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
            "script-src 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.template_filter("render_body")
    def render_body(text: str) -> Markup:
        """Escape the body, then fold quoted runs (lines starting with '>') into <details>."""
        out, run = [], []

        def flush():
            if run:
                n = len(run)
                out.append(
                    f'<details class="quoted"><summary>Quoted text ({n} line{"s" if n != 1 else ""})</summary>'
                    f'<div class="q">{escape(chr(10).join(run))}</div></details>'
                )
                run.clear()

        for line in text.split("\n"):
            if line.startswith(">"):
                run.append(line)
            else:
                flush()
                out.append(str(escape(line)) + "\n")
        flush()
        return Markup("".join(out))

    @app.template_filter("when")
    def when(iso: str | None, fmt: str = "%d %b %Y, %H:%M UTC") -> str:
        if not iso:
            return "date unknown"
        return datetime.fromisoformat(iso).strftime(fmt)

    @app.context_processor
    def _ctx():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(16)
        return {"MONTHS": MONTHS, "csrf": session["csrf"]}

    # ---- pages -------------------------------------------------------------

    @app.route("/")
    def home():
        rows = con().execute(
            "SELECT year, month, COUNT(*) n FROM visible GROUP BY year, month ORDER BY year, month"
        ).fetchall()
        years: dict[int, list[int]] = {}
        for r in rows:
            years.setdefault(r["year"], [0] * 12)[r["month"] - 1] = r["n"]
        total = sum(sum(v) for v in years.values())
        peak = max((max(v) for v in years.values()), default=1)
        return render_template("home.html", years=years, total=total, peak=peak)

    @app.route("/browse/<int:year>")
    def year_view(year):
        months = con().execute(
            "SELECT month, COUNT(*) n, COUNT(DISTINCT thread_id) t FROM visible WHERE year=? GROUP BY month ORDER BY month",
            (year,),
        ).fetchall()
        if not months:
            abort(404)
        return render_template("year.html", year=year, months=months)

    @app.route("/browse/<int:year>/<int:month>")
    def month_view(year, month):
        if not 1 <= month <= 12:
            abort(404)
        page = max(1, request.args.get("page", 1, type=int))
        rows = con().execute(
            """SELECT thread_id, MIN(sent_at) first_at, COUNT(*) n,
                      (SELECT subject FROM visible v2 WHERE v2.thread_id = v.thread_id ORDER BY sent_at LIMIT 1) subject,
                      (SELECT from_name FROM visible v2 WHERE v2.thread_id = v.thread_id ORDER BY sent_at LIMIT 1) starter
               FROM visible v WHERE year=? AND month=?
               GROUP BY thread_id ORDER BY first_at LIMIT ? OFFSET ?""",
            (year, month, PAGE + 1, (page - 1) * PAGE),
        ).fetchall()
        if not rows and page == 1:
            abort(404)
        n = con().execute("SELECT COUNT(*) FROM visible WHERE year=? AND month=?", (year, month)).fetchone()[0]
        return render_template("month.html", year=year, month=month, rows=rows[:PAGE], n=n,
                               page=page, more=len(rows) > PAGE)

    @app.route("/thread/<int:tid>")
    def thread(tid):
        t = con().execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        if not t:
            abort(404)
        msgs = con().execute(
            "SELECT * FROM visible WHERE thread_id=? ORDER BY sent_at, source_file, ordinal", (tid,)
        ).fetchall()
        withheld = t["n_messages"] - len(msgs)
        if not msgs:
            abort(410)
        names = {m["uid"]: m["from_name"] for m in msgs}
        return render_template("thread.html", t=t, msgs=msgs, names=names, withheld=withheld)

    @app.route("/m/<uid>")
    def message(uid):
        if not re.fullmatch(r"[0-9a-f]{12}(-\d+)?", uid):
            abort(404)
        m = con().execute("SELECT * FROM visible WHERE uid=?", (uid,)).fetchone()
        if not m:
            exists = con().execute("SELECT 1 FROM main.messages WHERE uid=?", (uid,)).fetchone()
            abort(410 if exists else 404)
        parent = None
        if m["parent_uid"]:
            parent = con().execute("SELECT uid, from_name, subject FROM visible WHERE uid=?", (m["parent_uid"],)).fetchone()
        replies = con().execute(
            "SELECT uid, from_name, sent_at FROM visible WHERE parent_uid=? ORDER BY sent_at", (uid,)
        ).fetchall()
        return render_template("message.html", m=m, parent=parent, replies=replies,
                               attachments=json.loads(m["attachments"]),
                               permalink=url_for("message", uid=uid, _external=True))

    @app.route("/search")
    def search():
        q = (request.args.get("q") or "").strip()[:200]
        who = (request.args.get("from") or "").strip()[:80]
        start = request.args.get("start", type=int)
        end = request.args.get("end", type=int)
        page = max(1, request.args.get("page", 1, type=int))
        results, by_year, error, n = [], [], None, 0
        if q or who:
            where, params = [], []
            if q:
                where.append("v.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
                params.append(fts_query(q))
            if who:
                where.append("v.from_name LIKE ?")
                params.append(f"%{who}%")
            if start:
                where.append("v.year >= ?")
                params.append(start)
            if end:
                where.append("v.year <= ?")
                params.append(end)
            w = " AND ".join(where)
            try:
                by_year = con().execute(f"SELECT year, COUNT(*) n FROM visible v WHERE {w} GROUP BY year ORDER BY year", params).fetchall()
                n = sum(r["n"] for r in by_year)
                if q:
                    sql = (f"SELECT v.uid, v.thread_id, v.from_name, v.subject, v.sent_at, "
                           f"snippet(messages_fts, 1, '\u0002', '\u0003', ' … ', 24) snip "
                           f"FROM messages_fts JOIN visible v ON v.id = messages_fts.rowid "
                           f"WHERE messages_fts MATCH ? AND {w} ORDER BY bm25(messages_fts) LIMIT ? OFFSET ?")
                    results = con().execute(sql, [fts_query(q), *params, PAGE + 1, (page - 1) * PAGE]).fetchall()
                else:
                    # Pick the page of ids from the covering index first, so bodies
                    # are read for 50 rows rather than for every match.
                    results = con().execute(
                        f"SELECT uid, thread_id, from_name, subject, sent_at, substr(body, 1, 200) snip "
                        f"FROM main.messages WHERE id IN (SELECT v.id FROM visible v WHERE {w} "
                        f"ORDER BY v.sent_at LIMIT ? OFFSET ?) ORDER BY sent_at",
                        [*params, PAGE + 1, (page - 1) * PAGE]).fetchall()
            except Exception:
                error = "That search could not be read. Try plain words, or put a phrase in double quotes."
        return render_template("search.html", q=q, who=who, start=start, end=end, results=results[:PAGE],
                               more=len(results) > PAGE, page=page, by_year=by_year, n=n, error=error)

    @app.template_filter("snip")
    def snip(text: str) -> Markup:
        s = str(escape(text or ""))
        return Markup(s.replace("\u0002", "<mark>").replace("\u0003", "</mark>"))

    @app.route("/timeline")
    def timeline():
        rows = con().execute(
            "SELECT year, month, COUNT(*) n FROM visible GROUP BY year, month ORDER BY year, month"
        ).fetchall()
        per_year = con().execute("SELECT year, COUNT(*) n, COUNT(DISTINCT thread_id) t FROM visible GROUP BY year ORDER BY year").fetchall()
        return render_template("timeline.html", rows=rows, per_year=per_year,
                               peak=max((r["n"] for r in rows), default=1))

    @app.route("/opt-out", methods=["GET", "POST"])
    def opt_out():
        errors, form = [], {}
        if request.method == "POST":
            form = {k: (request.form.get(k) or "").strip() for k in
                    ("name_as_posted", "other_names", "example_links", "contact", "relationship", "note")}
            if request.form.get("website"):  # honeypot
                return redirect(url_for("opt_out_thanks"))
            if request.form.get("csrf") != session.get("csrf"):
                errors.append("The form expired. Please send it again.")
            if not form["name_as_posted"]:
                errors.append("Give the name your messages were posted under.")
            if not form["contact"]:
                errors.append("Give a way to reach you so the request can be confirmed.")
            if form["relationship"] not in ("self", "representative", "estate"):
                errors.append("Say whose messages these are.")
            if any(len(v) > 4000 for v in form.values()):
                errors.append("One of the fields is too long.")
            if not errors:
                import sqlite3
                rc = sqlite3.connect(app.config["REQUESTS"])
                rc.execute(
                    "INSERT INTO optout_requests (name_as_posted, other_names, example_links, contact, relationship, note)"
                    " VALUES (:name_as_posted, :other_names, :example_links, :contact, :relationship, :note)", form)
                rc.commit()
                rc.close()
                return redirect(url_for("opt_out_thanks"))
        return render_template("optout.html", errors=errors, form=form)

    @app.route("/opt-out/received")
    def opt_out_thanks():
        return render_template("optout_thanks.html")

    @app.route("/about")
    def about():
        meta = dict(con().execute("SELECT key, value FROM meta").fetchall())
        cov = json.loads(meta.get("coverage", "{}"))
        return render_template("about.html", built=meta.get("built_at"), cov=cov)

    @app.route("/robots.txt")
    def robots():
        return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")

    @app.errorhandler(404)
    def nf(_e):
        return render_template("error.html", code=404, msg="Nothing here."), 404

    @app.errorhandler(410)
    def gone(_e):
        return render_template("error.html", code=410,
                               msg="This message has been withdrawn from the reader at the poster's request."), 410

    return app


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


app = create_app() if os.environ.get("WPAL_NO_AUTOAPP") != "1" else None
