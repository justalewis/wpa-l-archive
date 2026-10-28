"""Item 3 on the page: a confidence badge on every thread, links to likely
continuations, and a form to flag a bad split or merge for review.

Flags land in state.db (rate-limited, honeypot, CSRF) and are worked through
with `python -m wpal.review flags`; an accepted correction becomes a row in
decisions/thread_overrides.csv, which every later build applies.
"""

from __future__ import annotations

from flask import abort, redirect, request, url_for

from .. import db as dbmod
from ..limits import allow_write

FLAGS_PER_HOUR = 20
PROBLEMS = {"split": "This discussion continues in another thread",
            "merged": "Unrelated messages are joined here",
            "wrong-parent": "A reply is attached to the wrong message",
            "other": "Something else"}


def register(app) -> None:
    def has_confidence():
        return app.cached("has_confidence", lambda: "confidence_level" in {
            r[1] for r in app.con().execute("PRAGMA table_info(threads)")})

    def thread_hook(t):
        if not has_confidence():
            return {}
        cands = app.con().execute("""
            SELECT CASE WHEN s.a_key = :k THEN s.b_key ELSE s.a_key END other, s.shared_people, s.gap_days,
                   t.started_at, t.n_messages
            FROM split_candidates s JOIN threads t ON t.root_uid = CASE WHEN s.a_key = :k THEN s.b_key ELSE s.a_key END
            WHERE s.a_key = :k OR s.b_key = :k""", {"k": t["root_uid"]}).fetchall()
        methods = app.con().execute(
            "SELECT link_method, COUNT(*) n FROM main.messages WHERE thread_id = ? AND parent_uid IS NOT NULL "
            "GROUP BY link_method ORDER BY n DESC", (t["id"],)).fetchall()
        return {"confidence": {"level": t["confidence_level"], "score": t["confidence"], "reason": t["confidence_reason"],
                               "methods": methods},
                "continuations": cands, "flag_problems": PROBLEMS,
                "flagged": request.args.get("flagged") == "1"}

    app.hooks["thread"].append(thread_hook)

    @app.route("/t/<key>/flag", methods=["POST"])
    def thread_flag(key):
        if request.form.get("website") or not app.csrf_ok():
            abort(400)
        if not app.con().execute("SELECT 1 FROM threads WHERE root_uid = ?", (key,)).fetchone():
            abort(404)
        problem = request.form.get("problem")
        if problem not in PROBLEMS:
            abort(400)
        with dbmod.state(app.config["REQUESTS"]) as st:
            if not allow_write(st, "flag", FLAGS_PER_HOUR):
                abort(429)
            st.execute("INSERT INTO thread_flags (thread_key, problem, note) VALUES (?,?,?)",
                       (key, problem, (request.form.get("note") or "").strip()[:2000]))
        return redirect(url_for("thread", key=key, flagged=1) + "#flag")
