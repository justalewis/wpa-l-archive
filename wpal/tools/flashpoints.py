"""Item 8 on the page: ranked high-activity threads and weeks, with traces."""

from __future__ import annotations

import collections
from datetime import date, timedelta

from flask import abort, render_template, request

from ..app import NavItem


def register(app) -> None:
    app.nav.append(NavItem("flashpoints", "Flashpoints", "flashpoints", "ic-flashpoint", 54))

    def ready():
        return app.cached("has_flash", lambda: bool(app.con().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'flashpoints'").fetchone()))

    @app.route("/flashpoints")
    def flashpoints():
        if not ready():
            abort(404)
        con = app.con()
        kind = "week" if request.args.get("kind") == "week" else "thread"
        rows = con.execute("SELECT * FROM flashpoints WHERE kind = ? ORDER BY rank LIMIT 40", (kind,)).fetchall()
        items = []
        for r in rows:
            if kind == "thread":
                t = con.execute("SELECT * FROM threads WHERE root_uid = ?", (r["ref"],)).fetchone()
                if not t:
                    continue
                days = collections.Counter(d[0] for d in con.execute(
                    "SELECT substr(sent_at, 1, 10) FROM visible WHERE thread_id = ?", (t["id"],)))
                if not days:
                    continue  # every message withdrawn
                trace = _trace(days, r["start"][:10], r["finish"][:10])
                items.append({"r": r, "title": t["subject"], "href": app.url_for_thread(r["ref"]), "trace": trace})
            else:
                start = date.fromisoformat(r["ref"])
                days = collections.Counter(d[0] for d in con.execute(
                    "SELECT substr(sent_at, 1, 10) FROM main.messages WHERE sent_at >= ? AND sent_at < ?",
                    (start.isoformat(), (start + timedelta(days=7)).isoformat())))
                threads = con.execute(
                    "SELECT t.root_uid, t.subject, COUNT(*) n FROM visible v JOIN threads t ON t.id = v.thread_id "
                    "WHERE v.sent_at >= ? AND v.sent_at < ? GROUP BY t.id ORDER BY n DESC LIMIT 4",
                    (start.isoformat(), (start + timedelta(days=7)).isoformat())).fetchall()
                items.append({"r": r, "title": f"Week of {start.strftime('%d %B %Y')}", "threads": threads,
                              "href": None, "trace": _trace(days, r["start"], r["finish"])})
        return render_template("flashpoints.html", items=items, kind=kind, active_nav="flashpoints")

    app.url_for_thread = lambda key: f"/t/{key}"


def _trace(days: collections.Counter, start: str, finish: str, W=160, H=28):
    """A small bar trace of messages per day across the episode."""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(finish)
    n = min(max(1, (d1 - d0).days + 1), 60)
    vals = [days.get((d0 + timedelta(days=i)).isoformat(), 0) for i in range(n)]
    peak = max(vals) or 1
    bw = W / n
    return {"W": W, "H": H, "bars": [(round(i * bw, 1), round(H - v / peak * H, 1), round(max(bw - 1, 1), 1),
                                      round(v / peak * H, 1), v, (d0 + timedelta(days=i)).isoformat())
                                     for i, v in enumerate(vals)]}
