"""The WPA-L Archive Reader: a Flask app over data/archive.db.

    flask --app wpal.app run --port 5057

Every page is noindex (robots meta, X-Robots-Tag, robots.txt). Anything that
names a poster or shows text reads the `visible` view, so a suppression
applies everywhere at once. Anonymous counts (monthly volume, totals) read
the full table: suppressed posters stay in the aggregates and nowhere else
(decision of 2026-10-27, PLAN.md).
"""

from __future__ import annotations

import calendar
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape

from . import db as dbmod
from .cite import citations
from .icons import ICONS, STYLE_HASHES
from .query import Filters, fts_query, where  # noqa: F401  (fts_query re-exported for tests)

PAGE = 50
MONTHS = list(calendar.month_name)
UID_RE = re.compile(r"[0-9a-f]{12}(-\d+)?")


@dataclass
class NavItem:
    key: str
    label: str
    endpoint: str
    icon: str
    order: int = 50  # position in the capsa; core pages are 10, 20, ...


# Destinations in the capsa, in order. Features add themselves here.
NAV = [
    NavItem("browse", "Browse", "home", "ic-browse", 10),
    NavItem("search", "Search", "search", "ic-search", 20),
    NavItem("timeline", "Timeline", "timeline", "ic-timeline", 30),
    NavItem("about", "About", "about", "ic-about", 90),
    NavItem("optout", "Opt out", "opt_out", "ic-optout", 99),
]


def create_app(archive: str | None = None, requests_db: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.secret_key = os.environ.get("WPAL_SECRET") or secrets.token_hex(32)
    app.config["ARCHIVE"] = archive or dbmod.archive_path()
    app.config["REQUESTS"] = requests_db or dbmod.requests_path()
    app.config["CACHE"] = {}
    dbmod.init_requests(app.config["REQUESTS"])
    asset_v = str(int(max(os.path.getmtime(os.path.join(app.static_folder, f))
                          for f in ("wpa.css", "site.js", "tokens.css"))))

    def con():
        if "con" not in g:
            g.con = dbmod.connect(app.config["ARCHIVE"], app.config["REQUESTS"])
        return g.con

    def cached(key, fn):
        """Per-process cache for figures that only change with a rebuild."""
        c = app.config["CACHE"]
        if key not in c:
            c[key] = fn()
        return c[key]

    def csrf_ok() -> bool:
        """The form's token matches this session's. Both must be present."""
        sent, held = request.form.get("csrf"), session.get("csrf")
        return bool(sent and held and secrets.compare_digest(sent, held))

    app.con = con
    app.cached = cached
    app.csrf_ok = csrf_ok
    app.nav = list(NAV)  # tools insert their destinations into this copy
    # Features add context to core pages through these hooks (wpal/features.py).
    app.hooks = {"thread": [], "message": [], "search": [], "timeline": []}

    def extras(name, *args) -> dict:
        out = {}
        for fn in app.hooks[name]:
            out.update(fn(*args) or {})
        return out

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("con", None)
        if c is not None:
            c.close()

    @app.after_request
    def _headers(resp):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        # The two hashes allow exactly the icon sprite's accent styles
        # (tools/icons/build_icons.py) and nothing else inline.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            f"style-src 'self' 'unsafe-hashes' {' '.join(STYLE_HASHES)}; "
            "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ---- filters -----------------------------------------------------------

    @app.template_filter("render_body")
    def render_body(text: str) -> Markup:
        """Escape the body, then fold quoted runs (lines starting with '>') into <details>."""
        out, run = [], []
        icon = '<svg class="ic ic-sm ic-quiet" aria-hidden="true"><use href="#ic-quoted"/></svg>'

        def flush():
            if run:
                n = len(run)
                out.append(
                    f'<details class="quoted"><summary>{icon} Quoted text ({n} line{"s" if n != 1 else ""})</summary>'
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

    @app.template_filter("snip")
    def snip(text: str) -> Markup:
        s = str(escape(text or ""))
        return Markup(s.replace("\u0002", "<mark>").replace("\u0003", "</mark>"))

    @app.context_processor
    def _ctx():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(16)
        years = cached("capsa_years", lambda: [dict(r) for r in con().execute(
            "SELECT year, COUNT(*) n FROM main.messages GROUP BY year ORDER BY year")])
        return {
            "MONTHS": MONTHS,
            "csrf": session["csrf"],
            "nav_items": sorted(app.nav, key=lambda i: i.order),
            "url_rules": app.view_functions,
            "capsa_years": years,
            "asset_v": asset_v,
            "public_persons": public_persons(),
        }

    def public_persons() -> set:
        """Person ids that get a page and a link (item 1); empty until persons exist."""
        def load():
            try:
                return {r[0] for r in con().execute("SELECT person_id FROM persons WHERE public = 1")}
            except Exception:
                return set()
        base = cached("public_persons", load)
        if not base:
            return base
        hidden = {r[0] for r in con().execute("SELECT pid FROM temp.hidden_persons")}
        return base - hidden

    app.public_persons = public_persons

    # ---- pages -------------------------------------------------------------

    def month_counts():
        return cached("month_counts", lambda: [dict(r) for r in con().execute(
            "SELECT year, month, COUNT(*) n FROM main.messages GROUP BY year, month ORDER BY year, month")])

    @app.route("/")
    def home():
        years: dict[int, list[int]] = {}
        for r in month_counts():
            years.setdefault(r["year"], [0] * 12)[r["month"] - 1] = r["n"]
        total = sum(sum(v) for v in years.values())
        peak = max((max(v) for v in years.values()), default=1)
        return render_template("home.html", years=years, total=total, peak=peak, active_nav="browse")

    @app.route("/browse/<int:year>")
    def year_view(year):
        months = con().execute(
            "SELECT month, COUNT(*) n, COUNT(DISTINCT thread_id) t FROM main.messages WHERE year=? GROUP BY month ORDER BY month",
            (year,),
        ).fetchall()
        if not months:
            abort(404)
        return render_template("year.html", year=year, months=months, active_year=year)

    @app.route("/browse/<int:year>/<int:month>")
    def month_view(year, month):
        if not 1 <= month <= 12:
            abort(404)
        page = max(1, request.args.get("page", 1, type=int))
        rows = con().execute(
            """SELECT v.thread_id, MIN(v.sent_at) first_at, COUNT(*) n, t.root_uid thread_key,
                      (SELECT subject FROM visible v2 WHERE v2.thread_id = v.thread_id ORDER BY sent_at LIMIT 1) subject,
                      (SELECT from_name FROM visible v2 WHERE v2.thread_id = v.thread_id ORDER BY sent_at LIMIT 1) starter,
                      (SELECT person_id FROM visible v2 WHERE v2.thread_id = v.thread_id ORDER BY sent_at LIMIT 1) starter_pid
               FROM visible v JOIN threads t ON t.id = v.thread_id
               WHERE v.year=? AND v.month=?
               GROUP BY v.thread_id ORDER BY first_at LIMIT ? OFFSET ?""",
            (year, month, PAGE + 1, (page - 1) * PAGE),
        ).fetchall()
        n = con().execute("SELECT COUNT(*) FROM main.messages WHERE year=? AND month=?", (year, month)).fetchone()[0]
        if not n:
            abort(404)
        return render_template("month.html", year=year, month=month, rows=rows[:PAGE], n=n,
                               page=page, more=len(rows) > PAGE, active_year=year)

    def thread_by_key(key):
        t = con().execute("SELECT * FROM threads WHERE root_uid=?", (key,)).fetchone()
        if t:
            return t
        try:  # a thread whose root moved under an override (item 3)
            r = con().execute("SELECT new_key FROM thread_redirects WHERE old_key=?", (key,)).fetchone()
        except Exception:
            r = None
        if r:
            abort(redirect(url_for("thread", key=r[0]), 301))
        return None

    @app.route("/t/<key>")
    def thread(key):
        if not UID_RE.fullmatch(key):
            abort(404)
        t = thread_by_key(key)
        if not t:
            abort(404)
        msgs = con().execute(
            "SELECT * FROM visible WHERE thread_id=? ORDER BY sent_at, source_file, ordinal", (t["id"],)
        ).fetchall()
        withheld = t["n_messages"] - len(msgs)
        if not msgs:
            abort(410)
        names = {m["uid"]: m["from_name"] for m in msgs}
        return render_template("thread.html", t=t, msgs=msgs, names=names, withheld=withheld,
                               active_year=t["year"], **extras("thread", t))


    @app.route("/thread/<int:tid>")
    def thread_by_id(tid):
        """Old integer thread URLs. Integer ids change on rebuild; keys don't."""
        t = con().execute("SELECT root_uid FROM threads WHERE id=?", (tid,)).fetchone()
        if not t:
            abort(404)
        return redirect(url_for("thread", key=t[0]), 301)

    @app.route("/m/<uid>")
    def message(uid):
        if not UID_RE.fullmatch(uid):
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
        key = con().execute("SELECT root_uid FROM threads WHERE id=?", (m["thread_id"],)).fetchone()[0]
        permalink = url_for("message", uid=uid, _external=True)
        return render_template("message.html", m=m, parent=parent, replies=replies, thread_key=key,
                               attachments=json.loads(m["attachments"]), permalink=permalink,
                               citations=citations(m["from_name"], m["subject"], m["sent_at"], m["year"], m["month"], permalink),
                               active_year=m["year"], **extras("message", m))


    @app.route("/search")
    def search():
        f = Filters.from_args(request.args)
        page = max(1, request.args.get("page", 1, type=int))
        results, by_year, error, n = [], [], None, 0
        if not f.empty:
            w, params = where(f)
            try:
                by_year = con().execute(f"SELECT year, COUNT(*) n FROM visible v WHERE {w} GROUP BY year ORDER BY year", params).fetchall()
                n = sum(r["n"] for r in by_year)
                if f.q:
                    fts = f.fts_table
                    sql = (f"SELECT v.uid, v.from_name, v.person_id, v.subject, v.sent_at, t.root_uid thread_key, "
                           f"snippet({fts}, -1, '\u0002', '\u0003', ' … ', 24) snip "
                           f"FROM {fts} JOIN visible v ON v.id = {fts}.rowid JOIN threads t ON t.id = v.thread_id "
                           f"WHERE {fts} MATCH ? AND {w} ORDER BY bm25({fts}) LIMIT ? OFFSET ?")
                    results = con().execute(sql, [fts_query(f.q), *params, PAGE + 1, (page - 1) * PAGE]).fetchall()
                else:
                    # Pick the page of ids from the covering index first, so bodies
                    # are read for 50 rows rather than for every match.
                    results = con().execute(
                        f"SELECT m.uid, m.from_name, m.person_id, m.subject, m.sent_at, t.root_uid thread_key, "
                        f"substr(m.body, 1, 200) snip FROM main.messages m JOIN threads t ON t.id = m.thread_id "
                        f"WHERE m.id IN (SELECT v.id FROM visible v WHERE {w} ORDER BY v.sent_at LIMIT ? OFFSET ?) "
                        f"ORDER BY m.sent_at",
                        [*params, PAGE + 1, (page - 1) * PAGE]).fetchall()
            except Exception:
                error = "That search could not be read. Try plain words, or put a phrase in double quotes."

        def page_url(p):
            return url_for("search") + "?" + urlencode({**f.as_dict(), "page": p})

        return render_template("search.html", q=f.q, who_q=f.who, start=f.start, end=f.end, filters=f,
                               results=results[:PAGE], more=len(results) > PAGE, page=page, by_year=by_year,
                               n=n, error=error, page_url=page_url, active_nav="search", **extras("search", f, by_year))


    @app.route("/timeline")
    def timeline():
        rows = month_counts()
        per_year = cached("per_year", lambda: [dict(r) for r in con().execute(
            "SELECT year, COUNT(*) n, COUNT(DISTINCT thread_id) t FROM main.messages GROUP BY year ORDER BY year")])
        return render_template("timeline.html", rows=rows, per_year=per_year, active_nav="timeline",
                               peak=max((r["n"] for r in rows), default=1), **extras("timeline"))


    @app.route("/opt-out", methods=["GET", "POST"])
    def opt_out():
        errors, form = [], {}
        if request.method == "POST":
            form = {k: (request.form.get(k) or "").strip() for k in
                    ("name_as_posted", "other_names", "example_links", "contact", "relationship", "note")}
            if request.form.get("website"):  # honeypot
                return redirect(url_for("opt_out_thanks"))
            if not csrf_ok():
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
                with dbmod.state(app.config["REQUESTS"]) as rc:
                    rc.execute(
                        "INSERT INTO optout_requests (name_as_posted, other_names, example_links, contact, relationship, note)"
                        " VALUES (:name_as_posted, :other_names, :example_links, :contact, :relationship, :note)", form)
                return redirect(url_for("opt_out_thanks"))
        return render_template("optout.html", errors=errors, form=form, active_nav="optout")

    @app.route("/opt-out/received")
    def opt_out_thanks():
        return render_template("optout_thanks.html", active_nav="optout")

    @app.route("/about")
    def about():
        meta = dict(con().execute("SELECT key, value FROM meta").fetchall())
        cov = json.loads(meta.get("coverage", "{}"))
        return render_template("about.html", built=meta.get("built_at"), cov=cov, active_nav="about")

    @app.route("/design")
    def design():
        tokens = ["papyrus", "papyrus-deep", "paper", "gloss", "gloss-soft", "dilute", "dilute-light",
                  "terracotta", "terracotta-lift", "terracotta-wash", "faience", "faience-wash", "rule", "rule-strong"]
        cats = [f"cat-{i}" for i in range(1, 9)] + ["cat-other"]
        return render_template("design.html", icons=ICONS, tokens=tokens, cats=cats)

    @app.route("/robots.txt")
    def robots():
        return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")

    @app.errorhandler(404)
    def nf(_e):
        return render_template("error.html", code=404, msg="Nothing here."), 404

    @app.errorhandler(410)
    def gone(_e):
        return render_template("error.html", code=410,
                               msg="This has been withdrawn from the reader at the poster's request."), 410

    from . import features
    features.register(app)
    return app


app = create_app() if os.environ.get("WPAL_NO_AUTOAPP") != "1" else None
