"""Item 10 on the page: institutions by era, Carnegie shares per year,
institution pages, and the unmatched-name review list."""

from __future__ import annotations

from flask import abort, render_template, request

from ..app import NavItem
from ..stages.institutions import TYPE_ORDER
from ..stages.network import ERAS


def register(app) -> None:
    app.nav.append(NavItem("institutions", "Institutions", "institutions", "ic-institution", 56))

    def ready():
        return app.cached("has_inst", lambda: bool(app.con().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'institution_era'").fetchone()))

    @app.route("/institutions")
    def institutions():
        if not ready():
            abort(404)
        con = app.con()
        eras = [(a, b) for a, b in ERAS]
        label = [f"{a}–{b}" for a, b in eras]
        era = request.args.get("era") if request.args.get("era") in label else label[2]
        rows = con.execute("SELECT year, carnegie_type, posts FROM post_carnegie ORDER BY year").fetchall()
        years = {}
        for r in rows:
            years.setdefault(r["year"], {})[r["carnegie_type"]] = r["posts"]
        types = [t for t in TYPE_ORDER if any(t in v for v in years.values())]
        shares = {y: {t: v.get(t, 0) / max(1, sum(v.get(x, 0) for x in types)) for t in types} for y, v in years.items()}
        known = {y: sum(v.get(t, 0) for t in types) / max(1, sum(v.values())) for y, v in years.items()}
        # Top institutions in the era, by posts from affiliated people (aggregate: no names).
        top = con.execute("""
            SELECT i.unitid, i.name, i.state, i.carnegie_type, i.lat, i.lon, e.posts, e.people
            FROM institution_era e JOIN institutions i ON i.unitid = e.unitid
            WHERE e.era = ? ORDER BY e.posts DESC""", (era,)).fetchall()
        points = _project([r for r in top if r["lat"] and r["lon"]])
        unmatched = con.execute("SELECT * FROM unmatched_institutions ORDER BY n DESC LIMIT 40").fetchall()
        n_unmatched = con.execute("SELECT COUNT(*) FROM unmatched_institutions").fetchone()[0]
        return render_template("institutions.html", era=era, eras=label, years=years, types=types, shares=shares,
                               known=known, top=top[:25], points=points, unmatched=unmatched, n_unmatched=n_unmatched,
                               active_nav="institutions")

    @app.route("/institution/<int:unitid>")
    def institution(unitid):
        if not ready():
            abort(404)
        con = app.con()
        i = con.execute("SELECT * FROM institutions WHERE unitid = ?", (unitid,)).fetchone()
        if not i:
            abort(404)
        pub = app.public_persons()
        people = [r for r in con.execute("""
            SELECT pi.*, p.display_name FROM person_institutions pi JOIN persons p ON p.person_id = pi.person_id
            WHERE pi.unitid = ? ORDER BY pi.first_at""", (unitid,)) if r["person_id"] in pub]
        n_people = con.execute("SELECT COUNT(*) FROM person_institutions WHERE unitid = ?", (unitid,)).fetchone()[0]
        names = [r[0] for r in con.execute("SELECT name FROM institution_aliases WHERE unitid = ?", (unitid,))]
        jobs = con.execute("SELECT j.*, v.subject FROM jobs j CROSS JOIN visible v ON v.uid = j.uid WHERE j.unitid = ? "
                           "ORDER BY j.posted_at", (unitid,)).fetchall()
        per_year = dict(con.execute("""
            SELECT m.year, COUNT(*) FROM main.messages m JOIN person_institutions pi ON pi.person_id = m.person_id
            WHERE pi.unitid = ? AND substr(m.sent_at, 1, 10) BETWEEN substr(pi.first_at, 1, 10) AND substr(pi.last_at, 1, 10)
            GROUP BY m.year""", (unitid,)).fetchall())
        return render_template("institution.html", i=i, people=people, n_people=n_people, names=names, jobs=jobs,
                               per_year=per_year, peak=max(per_year.values(), default=1), active_nav="institutions")


def _project(rows, W=900, H=520):
    """Equirectangular placement for the contiguous US, Alaska and Hawaii inset; no basemap."""
    out = []
    peak = max((r["posts"] for r in rows), default=1)
    for r in rows:
        lat, lon = r["lat"], r["lon"]
        if lon < -130 or lat < 24 or lat > 50:
            continue  # outside the contiguous states; listed in the table
        x = (lon + 125) / 59 * (W - 40) + 20
        y = (50 - lat) / 26 * (H - 40) + 20
        out.append({"x": round(x, 1), "y": round(y, 1), "r": round(3 + 14 * (r["posts"] / peak) ** 0.5, 1),
                    "name": r["name"], "posts": r["posts"], "unitid": r["unitid"], "type": r["carnegie_type"]})
    return {"W": W, "H": H, "points": out}
