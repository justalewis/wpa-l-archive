"""Item 7 on the page: era networks, concentration, cohorts and roles.

Ethics thresholds apply here: a person is named only with a public page (25+
posts, not withdrawn); an edge is drawn only at 3+ exchanges; withdrawn people
are left out of the drawing entirely, and others below the threshold appear
as unlabeled points.
"""

from __future__ import annotations

import json

from flask import abort, render_template, request

from ..app import NavItem
from ..stages.network import ERAS
from .people import EDGE_MIN


def register(app) -> None:
    app.nav.append(NavItem("participation", "Participation", "participation", "ic-network", 52))

    def ready():
        return app.cached("has_network", lambda: bool(app.con().execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'era_graphs'").fetchone()))

    @app.route("/participation")
    def participation():
        if not ready():
            abort(404)
        eras = [f"{a}–{b}" for a, b in ERAS]
        era = request.args.get("era") if request.args.get("era") in eras else eras[2]
        scope = "strong" if request.args.get("links") == "strong" else "all"
        con = app.con()
        pub = app.public_persons()
        hidden = {r[0] for r in con.execute("SELECT pid FROM temp.hidden_persons")}
        layout = json.loads(con.execute("SELECT layout FROM era_graphs WHERE era = ? AND scope = ?", (era, scope)).fetchone()[0])
        names = dict(con.execute("SELECT person_id, display_name FROM persons WHERE public = 1"))
        nodes = [n for n in layout["nodes"] if n["id"] not in hidden]
        keep = {n["id"] for n in nodes}
        edges = [e for e in layout["edges"] if e["a"] in keep and e["b"] in keep and e["w"] >= EDGE_MIN]
        graph = _draw(nodes, edges, names, pub)
        top = [r for r in con.execute(
            "SELECT s.*, p.display_name FROM person_era_stats s JOIN persons p ON p.person_id = s.person_id "
            "WHERE s.era = ? AND s.posts >= 25 ORDER BY s.betweenness DESC, s.strength DESC LIMIT 60", (era,))
            if r["person_id"] in pub][:20]
        conc = con.execute("SELECT * FROM concentration ORDER BY year").fetchall()
        cohorts = {}
        for r in con.execute("SELECT * FROM cohorts ORDER BY first_year"):
            cohorts.setdefault(r["first_year"], {"size": r["cohort_size"]})[r["after_years"]] = r["retained"]
        roles = con.execute("SELECT * FROM era_roles").fetchall()
        return render_template("participation.html", eras=eras, era=era, scope=scope, graph=graph, top=top, conc=conc,
                               cohorts=cohorts, roles=roles, edge_min=EDGE_MIN, n_hidden=len(hidden & {n["id"] for n in layout["nodes"]}),
                               active_nav="participation")


def _draw(nodes, edges, names, pub, W=900, H=640, pad=40):
    if not nodes:
        return None
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    def sx(v):
        return pad + (v - x0) / ((x1 - x0) or 1) * (W - 2 * pad)

    def sy(v):
        return pad + (v - y0) / ((y1 - y0) or 1) * (H - 2 * pad)

    wmax = max(n["w"] for n in nodes) or 1
    pos = {n["id"]: (round(sx(n["x"]), 1), round(sy(n["y"]), 1)) for n in nodes}
    emax = max((e["w"] for e in edges), default=1)
    out_nodes = []
    for n in nodes:
        named = n["id"] in pub and n["id"] in names
        out_nodes.append({"id": n["id"], "x": pos[n["id"]][0], "y": pos[n["id"]][1],
                          "r": round(4 + 10 * (n["w"] / wmax) ** 0.5, 1),
                          "label": names[n["id"]] if named else None, "w": n["w"]})
    out_edges = [{"x1": pos[e["a"]][0], "y1": pos[e["a"]][1], "x2": pos[e["b"]][0], "y2": pos[e["b"]][1],
                  "sw": round(0.6 + 3 * e["w"] / emax, 2), "w": e["w"]} for e in edges]
    return {"W": W, "H": H, "nodes": out_nodes, "edges": out_edges}
