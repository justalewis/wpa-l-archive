"""Item 2: saved corpora, exports, the JSON API, and the /data and /ethics pages.

Every surface here builds on wpal.query, so the page, a saved corpus, an
export and the API agree on what a search matches, and all read through the
`visible` view: a withdrawn poster's messages never leave the reader.

A saved corpus freezes the message uids that matched when it was saved,
with the build it came from. Message uids are stable across rebuilds, so the
corpus returns the same ids after any rebuild, less any message withdrawn
since (counted, never shown).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import secrets
import string

from flask import (Response, abort, jsonify, redirect, render_template, request, send_file, session,
                   stream_with_context, url_for)

from .. import bulk
from .. import db as dbmod
from ..app import NavItem
from ..cite import citations
from ..limits import allow_read, allow_write
from ..query import Filters, where

EXPORT_MAX = 25_000     # messages per export or corpus
API_PAGE = 50
API_PER_MINUTE = 60
CORPORA_PER_HOUR = 10
FIELDS = ["message_id", "thread_id", "poster_id", "poster_name", "date_utc", "year", "month",
          "genre", "subject", "body", "body_own_words", "permalink"]


def register(app) -> None:
    app.nav.append(NavItem("data", "Data", "data_page", "ic-data", 80))
    app.nav.append(NavItem("ethics", "Ethics", "ethics", "ic-method", 95))

    def build_id():
        return app.cached("build_id", lambda: (app.con().execute(
            "SELECT value FROM meta WHERE key IN ('build_id', 'built_at') ORDER BY key LIMIT 1").fetchone() or [""])[0])

    def has_fresh():
        return app.cached("has_fresh", lambda: "body_fresh" in {
            r[1] for r in app.con().execute("PRAGMA table_info(messages)")})

    # ---- pages -------------------------------------------------------------

    @app.route("/data", methods=["GET", "POST"])
    def data_page():
        if request.method == "POST":
            if app.csrf_ok() and request.form.get("agree") == "yes":
                session["terms"] = True
            return redirect(request.form.get("next") or url_for("data_page"))
        meta = dict(app.con().execute("SELECT key, value FROM meta").fetchall())
        ready = bulk_file()
        return render_template("data.html", fields=FIELDS, bundle=ready, bundle_mb=ready and ready.stat().st_size / 1e6,
                               bundle_files=[n for n, _, _ in bulk.TABLES], build=build_id(), meta=meta, export_max=EXPORT_MAX,
                               api_page=API_PAGE, api_rate=API_PER_MINUTE, agreed=session.get("terms"),
                               next=request.args.get("next", ""), active_nav="data")

    @app.route("/ethics")
    def ethics():
        from ..stages.identity import PUBLIC_MIN_POSTS
        from .people import EDGE_MIN
        return render_template("ethics.html", person_min=PUBLIC_MIN_POSTS, edge_min=EDGE_MIN, active_nav="ethics")

    # ---- the whole dataset -------------------------------------------------

    def bulk_file():
        path = bulk.bundle_path(app.config["ARCHIVE"], bulk.bundle_key(app.con()))
        return path if path.exists() else None

    @app.route("/data/download")
    def data_download():
        if not session.get("terms"):
            return redirect(url_for("data_page", next=request.full_path) + "#terms")
        key = bulk.bundle_key(app.con())
        path = bulk.bundle_path(app.config["ARCHIVE"], key)
        if path.exists():
            return send_file(path, as_attachment=True, download_name=f"{bulk.STEM}-{build_id()[:10]}.zip",
                             mimetype="application/zip", max_age=3600)
        bulk.build_in_background(app.config["ARCHIVE"], app.config["REQUESTS"], request.url_root, key)
        return render_template("error.html", code="Preparing the download",
                               msg="The full dataset is being packaged right now. The first download after an update takes "
                                   "a few minutes, so reload this page in a bit and the download will start."), 202

    # ---- corpora -----------------------------------------------------------

    @app.route("/corpus", methods=["POST"])
    def corpus_create():
        if request.form.get("website"):  # honeypot
            abort(400)
        if not app.csrf_ok():
            abort(400)
        name = (request.form.get("name") or "").strip()[:120]
        if not name:
            return redirect(request.referrer or url_for("search"))
        f = Filters.from_args(request.form)
        if f.empty:
            abort(400)
        w, params = where(f)
        uids = [r[0] for r in app.con().execute(
            f"SELECT uid FROM visible v WHERE {w} ORDER BY sent_at LIMIT ?", [*params, EXPORT_MAX + 1])]
        if len(uids) > EXPORT_MAX:
            return render_template("error.html", code=413,
                                   msg=f"That search matches more than {EXPORT_MAX:,} messages. Narrow it before saving."), 413
        cid = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
        with dbmod.state(app.config["REQUESTS"]) as st:
            if not allow_write(st, "corpus", CORPORA_PER_HOUR):
                return render_template("error.html", code=429, msg="That's a lot of saved searches from here this hour. Please try again a little later."), 429
            st.execute("INSERT INTO corpora (id, name, description, query_json, uids_json, n, build_id) VALUES (?,?,?,?,?,?,?)",
                       (cid, name, (request.form.get("description") or "").strip()[:1000],
                        json.dumps(f.as_dict()), json.dumps(uids), len(uids), build_id()))
        return redirect(url_for("corpus_view", cid=cid))

    def load_corpus(cid):
        if not cid.isalnum() or len(cid) != 10:
            abort(404)
        c = app.con().execute("SELECT * FROM req.corpora WHERE id = ?", (cid,)).fetchone()
        if not c:
            abort(404)
        return c

    def corpus_rows(c, limit=None, offset=0):
        """The snapshot's messages that are still visible, in date order."""
        uids = json.loads(c["uids_json"])
        con = app.con()
        con.execute("CREATE TEMP TABLE IF NOT EXISTS _snap (uid TEXT PRIMARY KEY)")
        con.execute("DELETE FROM _snap")
        con.executemany("INSERT OR IGNORE INTO _snap VALUES (?)", [(u,) for u in uids])
        sql = ("SELECT v.*, t.root_uid thread_key FROM visible v JOIN temp._snap s ON s.uid = v.uid "
               "JOIN threads t ON t.id = v.thread_id ORDER BY v.sent_at")
        if limit:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return con.execute(sql).fetchall()

    @app.route("/corpus/<cid>")
    def corpus_view(cid):
        c = load_corpus(cid)
        page = max(1, request.args.get("page", 1, type=int))
        rows = corpus_rows(c, 51, (page - 1) * 50)
        n_visible = len(corpus_rows(c))
        return render_template("corpus.html", c=c, query=json.loads(c["query_json"]), rows=rows[:50],
                               more=len(rows) > 50, page=page, n_visible=n_visible,
                               withdrawn=c["n"] - n_visible, build=build_id(), active_nav="data")

    # ---- exports -----------------------------------------------------------

    def export_rows(rows, pseudo_seed: str | None):
        """Dicts in FIELDS order. With a seed, names and ids become stable pseudonyms."""
        pseud: dict[str, str] = {}
        for m in rows:
            pid = m["person_id"] or ""
            name = m["from_name"]
            if pseudo_seed is not None:
                if pid not in pseud:
                    pseud[pid] = "P" + hmac.new(pseudo_seed.encode(), pid.encode(), hashlib.sha256).hexdigest()[:8]
                pid = name = pseud[pid]
            yield {
                "message_id": m["uid"], "thread_id": m["thread_key"], "poster_id": pid, "poster_name": name,
                "date_utc": m["sent_at"] or "", "year": m["year"], "month": m["month"],
                "genre": m["genre"] if "genre" in m.keys() else "", "subject": m["subject"],
                "body": m["body"], "body_own_words": m["body_fresh"] if has_fresh() else "",
                "permalink": url_for("message", uid=m["uid"], _external=True),
            }

    def stream(rows, fmt, filename, pseudo_seed):
        def gen_csv():
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=FIELDS)
            w.writeheader()
            for r in export_rows(rows, pseudo_seed):
                w.writerow(r)
                if buf.tell() > 65536:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()

        def gen_jsonl():
            for r in export_rows(rows, pseudo_seed):
                yield json.dumps(r, ensure_ascii=False) + "\n"

        gen, mime = (gen_csv, "text/csv") if fmt == "csv" else (gen_jsonl, "application/x-ndjson")
        return Response(stream_with_context(gen()), mimetype=f"{mime}; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.route("/export")
    def export():
        if not session.get("terms"):
            return redirect(url_for("data_page", next=request.full_path) + "#terms")
        fmt = request.args.get("format", "csv")
        if fmt not in ("csv", "jsonl"):
            abort(400)
        pseudo = request.args.get("pseudo") == "1"
        cid = request.args.get("corpus")
        if cid:
            c = load_corpus(cid)
            rows = corpus_rows(c)
            seed, stem = f"corpus:{cid}", f"wpa-l-corpus-{cid}"
        else:
            f = Filters.from_args(request.args)
            if f.empty:
                abort(400)
            w, params = where(f)
            rows = app.con().execute(
                f"SELECT v.*, t.root_uid thread_key FROM visible v JOIN threads t ON t.id = v.thread_id "
                f"WHERE {w} ORDER BY v.sent_at LIMIT ?", [*params, EXPORT_MAX + 1]).fetchall()
            if len(rows) > EXPORT_MAX:
                return render_template("error.html", code=413,
                                       msg=f"That search matches more than {EXPORT_MAX:,} messages. Narrow it, or save it as a corpus in parts."), 413
            seed, stem = "search:" + json.dumps(f.as_dict(), sort_keys=True), "wpa-l-search"
        # A pseudonymized export keys its pseudonyms on the corpus (or the exact
        # query) plus the app secret: stable for re-downloads, unlinkable across them.
        pseudo_seed = (app.secret_key + "|" + seed) if pseudo else None
        return stream(rows, fmt, f"{stem}{'-pseudonymized' if pseudo else ''}.{fmt}", pseudo_seed)

    # ---- API ---------------------------------------------------------------

    def api_message(m, thread_key):
        url = url_for("message", uid=m["uid"], _external=True)
        return {"id": m["uid"], "thread_id": thread_key, "poster_id": m["person_id"], "poster_name": m["from_name"],
                "date_utc": m["sent_at"], "year": m["year"], "month": m["month"],
                "genre": m["genre"] if "genre" in m.keys() else None, "subject": m["subject"],
                "body": m["body"], "parent_id": m["parent_uid"], "permalink": url,
                "citation": dict(citations(m["from_name"], m["subject"], m["sent_at"], m["year"], m["month"], url))}

    @app.before_request
    def _api_limit():
        if request.path.startswith("/api/") and not allow_read("api", API_PER_MINUTE):
            return jsonify(error="rate limited", limit_per_minute=API_PER_MINUTE), 429

    @app.route("/api/v1/messages/<uid>")
    def api_messages(uid):
        m = app.con().execute("SELECT v.*, t.root_uid thread_key FROM visible v JOIN threads t ON t.id = v.thread_id "
                              "WHERE v.uid = ?", (uid,)).fetchone()
        if not m:
            return jsonify(error="not found"), 404
        return jsonify(api_message(m, m["thread_key"]))

    @app.route("/api/v1/threads/<key>")
    def api_threads(key):
        t = app.con().execute("SELECT * FROM threads WHERE root_uid = ?", (key,)).fetchone()
        if not t:
            return jsonify(error="not found"), 404
        msgs = app.con().execute("SELECT * FROM visible WHERE thread_id = ? ORDER BY sent_at", (t["id"],)).fetchall()
        return jsonify({"id": key, "subject": t["subject"], "started_at": t["started_at"], "last_at": t["last_at"],
                        "n_messages": len(msgs), "n_withdrawn": t["n_messages"] - len(msgs),
                        "messages": [api_message(m, key) for m in msgs]})

    @app.route("/api/v1/search")
    def api_search():
        f = Filters.from_args(request.args)
        if f.empty:
            return jsonify(error="give q, from, or genre"), 400
        page = max(1, request.args.get("page", 1, type=int))
        w, params = where(f)
        try:
            total = app.con().execute(f"SELECT COUNT(*) FROM visible v WHERE {w}", params).fetchone()[0]
            ids = [r[0] for r in app.con().execute(
                f"SELECT v.id FROM visible v WHERE {w} ORDER BY v.sent_at LIMIT ? OFFSET ?",
                [*params, API_PAGE, (page - 1) * API_PAGE])]
        except Exception:
            return jsonify(error="query could not be read"), 400
        rows = app.con().execute(
            f"SELECT m.*, t.root_uid thread_key FROM main.messages m JOIN threads t ON t.id = m.thread_id "
            f"WHERE m.id IN ({','.join('?' * len(ids))}) ORDER BY m.sent_at", ids).fetchall() if ids else []
        nxt = url_for("api_search", **f.as_dict(), page=page + 1, _external=True) if page * API_PAGE < total else None
        return jsonify({"query": f.as_dict(), "total": total, "page": page, "page_size": API_PAGE, "next": nxt,
                        "results": [api_message(m, m["thread_key"]) for m in rows]})

    @app.route("/api/v1/corpus/<cid>")
    def api_corpus(cid):
        c = load_corpus(cid)
        page = max(1, request.args.get("page", 1, type=int))
        rows = corpus_rows(c, API_PAGE + 1, (page - 1) * API_PAGE)
        return jsonify({"id": cid, "name": c["name"], "query": json.loads(c["query_json"]), "build_id": c["build_id"],
                        "created_at": c["created_at"], "n_saved": c["n"], "message_ids": json.loads(c["uids_json"]),
                        "page": page, "results": [api_message(m, m["thread_key"]) for m in rows[:API_PAGE]]})

    @app.route("/api/v1/posters/<pid>")
    def api_posters(pid):
        if pid not in app.public_persons():
            return jsonify(error="not found"), 404
        p = app.con().execute("SELECT * FROM persons WHERE person_id = ?", (pid,)).fetchone()
        aliases = app.con().execute("SELECT name, n FROM person_aliases WHERE person_id = ? ORDER BY n DESC", (pid,)).fetchall()
        affs = app.con().execute("SELECT institution, first_at, last_at, n FROM affiliations WHERE person_id = ?", (pid,)).fetchall()
        return jsonify({"id": pid, "name": p["display_name"], "first_at": p["first_at"], "last_at": p["last_at"],
                        "n_posts": p["n_posts"], "n_started": p["n_started"], "n_replies": p["n_replies"],
                        "names_as_posted": [dict(a) for a in aliases], "affiliations": [dict(a) for a in affs],
                        "page": url_for("person", pid=pid, _external=True)})

    @app.route("/api/v1/")
    def api_index():
        return jsonify({"version": 1, "build_id": build_id(), "docs": url_for("data_page", _external=True) + "#api",
                        "endpoints": ["/api/v1/messages/{id}", "/api/v1/threads/{id}", "/api/v1/search?q=&from=&start=&end=&page=",
                                      "/api/v1/corpus/{id}", "/api/v1/posters/{id}"]})
