"""Item 1: person pages and the people index.

Only persons with at least 25 posts (identity.PUBLIC_MIN_POSTS) who have not
been withdrawn get a page; everyone else stays a name on their messages. A
withdrawn person's page is gone (410), not merely unlinked.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for

from ..app import NavItem
from ..stages.identity import PUBLIC_MIN_POSTS

EDGE_MIN = 3  # ethics threshold for showing a pair of people (PLAN.md decision 2)


def register(app) -> None:
    app.nav.append(NavItem("people", "People", "people", "ic-person", 50))

    def has_persons():
        def check():
            try:
                app.con().execute("SELECT 1 FROM persons LIMIT 1").fetchone()
                return True
            except Exception:
                return False
        return app.cached("has_persons", check)

    @app.route("/people")
    def people():
        if not has_persons():
            abort(404)
        sort = request.args.get("sort", "posts")
        order = {"posts": "n_posts DESC", "name": "display_name COLLATE NOCASE", "first": "first_at"}.get(sort, "n_posts DESC")
        pub = app.public_persons()
        rows = [r for r in app.con().execute(f"SELECT * FROM persons WHERE public = 1 ORDER BY {order}") if r["person_id"] in pub]
        return render_template("people.html", rows=rows, sort=sort, threshold=PUBLIC_MIN_POSTS, active_nav="people")

    @app.route("/person/<pid>")
    def person(pid):
        con = app.con()
        p = con.execute("SELECT * FROM persons WHERE person_id = ?", (pid,)).fetchone()
        if not p:
            r = con.execute("SELECT new_id FROM person_redirects WHERE old_id = ?", (pid,)).fetchone() if has_persons() else None
            if r:
                return redirect(url_for("person", pid=r[0]), 301)
            abort(404)
        if pid in {x[0] for x in con.execute("SELECT pid FROM temp.hidden_persons")}:
            abort(410)
        if not p["public"]:
            abort(404)
        aliases = con.execute("SELECT name, n FROM person_aliases WHERE person_id = ? ORDER BY n DESC", (pid,)).fetchall()
        per_year = {r[0]: r[1] for r in con.execute(
            "SELECT year, COUNT(*) FROM main.messages WHERE person_id = ? GROUP BY year", (pid,))}
        affs = con.execute(
            "SELECT institution, first_at, last_at, n FROM affiliations WHERE person_id = ? ORDER BY first_at", (pid,)).fetchall()
        pub = app.public_persons()
        partners = [r for r in con.execute("""
            SELECT other, SUM(n) n FROM (
                SELECT p.person_id other, COUNT(*) n FROM main.messages c JOIN main.messages p ON p.uid = c.parent_uid
                WHERE c.person_id = :pid AND p.person_id != :pid GROUP BY p.person_id
                UNION ALL
                SELECT c.person_id other, COUNT(*) n FROM main.messages p JOIN main.messages c ON c.parent_uid = p.uid
                WHERE p.person_id = :pid AND c.person_id != :pid GROUP BY c.person_id)
            GROUP BY other HAVING SUM(n) >= :m ORDER BY n DESC LIMIT 40""", {"pid": pid, "m": EDGE_MIN})
            if r["other"] in pub][:12]
        names = {r[0]: r[1] for r in con.execute(
            f"SELECT person_id, display_name FROM persons WHERE person_id IN ({','.join('?' * len(partners))})",
            [r["other"] for r in partners])} if partners else {}
        recent = con.execute("""
            SELECT v.uid, v.subject, v.sent_at, t.root_uid thread_key, v.uid = t.root_uid started
            FROM visible v JOIN threads t ON t.id = v.thread_id
            WHERE v.person_id = ? ORDER BY v.sent_at DESC LIMIT 15""", (pid,)).fetchall()
        return render_template("person.html", p=p, aliases=aliases, per_year=per_year, affs=affs,
                               partners=partners, names=names, recent=recent, edge_min=EDGE_MIN,
                               peak=max(per_year.values(), default=1), active_nav="people")
