"""Item 9 on the page: most-mentioned works and scholars by era, list
mentions against Pinakes citation counts, a page per work, linked works on
message pages, and an export of the mention table.

Counts are anonymous aggregates and include withdrawn posters' messages; any
list of messages, and the export, reads the `visible` view.
"""

from __future__ import annotations

import csv
import io

from flask import Response, abort, redirect, render_template, request, session, url_for

from ..app import NavItem
from ..stages.citations import spearman
from ..stages.network import ERAS

PINAKES = "https://pinakes.wacclearinghouse.org"


def register(app) -> None:
    app.nav.append(NavItem("cited", "Cited works", "cited", "ic-citation", 58))

    def ready():
        return app.cached("has_cited", lambda: bool(app.con().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'cited_works'").fetchone()))

    @app.route("/cited")
    def cited():
        if not ready():
            abort(404)
        con = app.con()
        labels = [f"{a}–{b}" for a, b in ERAS]
        era = request.args.get("era")
        a, b = ERAS[labels.index(era)] if era in labels else (1993, 2021)
        works = con.execute("""
            SELECT w.*, COUNT(*) n_era FROM mentions m JOIN cited_works w ON w.work_id = m.work_id
            WHERE m.status = 'accepted' AND m.year BETWEEN ? AND ? GROUP BY w.work_id ORDER BY n_era DESC LIMIT 40""",
                            (a, b)).fetchall()
        scholars = con.execute("""
            SELECT name, COUNT(DISTINCT uid) n FROM scholar_mentions WHERE year BETWEEN ? AND ?
            GROUP BY name ORDER BY n DESC LIMIT 40""", (a, b)).fetchall()
        allw = con.execute("SELECT n_messages, pinakes_cites FROM cited_works").fetchall()
        rho = spearman([r[0] for r in allw], [r[1] for r in allw])
        totals = dict(con.execute("SELECT status, COUNT(*) FROM mentions GROUP BY status").fetchall())
        return render_template("cited.html", works=works, scholars=scholars, era=era or "", eras=labels, rho=rho,
                               n_works=len(allw), totals=totals, pinakes=PINAKES, active_nav="cited")

    @app.route("/cited/work/<int:wid>")
    def cited_work(wid):
        if not ready():
            abort(404)
        w = app.con().execute("SELECT * FROM cited_works WHERE work_id = ?", (wid,)).fetchone()
        if not w:
            abort(404)
        msgs = app.con().execute("""
            SELECT v.uid, v.subject, v.from_name, v.person_id, v.sent_at, m.kind, m.raw
            FROM mentions m JOIN visible v ON v.uid = m.uid
            WHERE m.work_id = ? AND m.status = 'accepted' ORDER BY v.sent_at""", (wid,)).fetchall()
        return render_template("cited_work.html", w=w, msgs=msgs, pinakes=PINAKES, active_nav="cited")

    @app.route("/cited/mentions.csv")
    def cited_export():
        if not session.get("terms"):
            return redirect(url_for("data_page", next=request.full_path) + "#terms")
        rows = app.con().execute("""
            SELECT m.uid, v.year, m.kind, m.raw, m.work_id, m.work_title, m.confidence, m.status
            FROM mentions m JOIN visible v ON v.uid = m.uid ORDER BY v.sent_at""").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["message_id", "year", "kind", "matched_text", "pinakes_work_id", "work_title", "confidence", "status"])
        w.writerows([tuple(r) for r in rows])
        return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="wpa-l-mentions.csv"'})

    def message_works(m):
        if not ready():
            return {}
        works = app.con().execute(
            "SELECT DISTINCT w.work_id, w.title, w.year FROM mentions x JOIN cited_works w ON w.work_id = x.work_id "
            "WHERE x.uid = ? AND x.status = 'accepted'", (m["uid"],)).fetchall()
        return {"linked_works": works}

    app.hooks["message"].append(message_works)
