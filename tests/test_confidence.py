"""Item 3: link methods, thread confidence, flags, and overrides through rebuilds."""

import os
import shutil
import sqlite3

import pytest

os.environ["WPAL_NO_AUTOAPP"] = "1"
os.environ["WPAL_PUBLIC_MIN_POSTS"] = "1"

from wpal import build  # noqa: E402
from wpal.app import create_app  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NAMES = {"era1993.txt": "1993-03.txt", "era2011_pair.txt": "2011-07.txt", "era2020_reply.txt": "2020-03.txt"}


def make(d, decisions, name):
    raw = d / "raw"
    if not raw.exists():
        raw.mkdir()
        for src, dest in NAMES.items():
            shutil.copy(os.path.join(FIX, src), raw / dest)
    db = d / name
    build.main(["--raw", str(raw), "--db", str(db), "--salt", str(d / "salt"), "--decisions", str(decisions),
                "--report", str(d / f"{name}.md"), "--workers", "1"])
    return db


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    d = tmp_path_factory.mktemp("conf")
    dec = d / "decisions"
    dec.mkdir()
    return d, dec, make(d, dec, "a.db")


def q(db, sql, *a):
    return sqlite3.connect(db).execute(sql, a).fetchall()


def test_every_reply_has_a_link_method(base):
    _, _, db = base
    rows = dict(q(db, "SELECT link_method, COUNT(*) FROM messages WHERE parent_uid IS NOT NULL GROUP BY 1"))
    assert rows.get("message-id", 0) >= 2          # 2011 and 2020 replies
    assert rows.get("subject-window", 0) + rows.get("wrote-line", 0) >= 2  # 1993 replies
    assert not q(db, "SELECT 1 FROM messages WHERE parent_uid IS NOT NULL AND link_method IS NULL")


def test_threads_show_method_and_confidence(base):
    d, _, db = base
    app = create_app(str(db), str(d / "s.db"))
    c = app.test_client()
    for key, n in q(db, "SELECT root_uid, n_messages FROM threads WHERE n_messages > 1"):
        page = c.get(f"/t/{key}").data.decode()
        assert "confidence." in page and "How this thread was put together" in page
    lv = dict(q(db, "SELECT root_uid, confidence_level FROM threads WHERE n_messages > 1"))
    header_thread = q(db, "SELECT t.root_uid FROM threads t JOIN messages m ON m.thread_id = t.id "
                          "WHERE m.link_method = 'message-id' LIMIT 1")[0][0]
    subject_thread = q(db, "SELECT t.root_uid FROM threads t JOIN messages m ON m.thread_id = t.id "
                           "WHERE m.year = 1993 AND m.parent_uid IS NOT NULL LIMIT 1")[0][0]
    rank = {"low": 0, "medium": 1, "high": 2}
    assert rank[lv[header_thread]] > rank[lv[subject_thread]]


def test_flag_is_queued(base):
    d, _, db = base
    state = d / "flags.db"
    app = create_app(str(db), str(state))
    c = app.test_client()
    key = q(db, "SELECT root_uid FROM threads WHERE n_messages > 1 LIMIT 1")[0][0]
    page = c.get(f"/t/{key}").data.decode()
    token = page.split('name="csrf" value="')[1].split('"')[0]
    r = c.post(f"/t/{key}/flag", data={"csrf": token, "problem": "merged", "note": "x"})
    assert r.status_code == 302
    assert q(state, "SELECT thread_key, problem, status FROM thread_flags") == [(key, "merged", "new")]
    assert c.post(f"/t/{key}/flag", data={"problem": "merged"}).status_code == 400


def test_overrides_survive_rebuild_and_redirect(base, tmp_path):
    d, _, db = base
    # In 1993, "placement" and "Re: placement" thread together; split the reply off,
    # and merge the "ccitation ethics" thread into the 2020 admin thread.
    reply = q(db, "SELECT uid FROM messages WHERE subject = 'Re: placement'")[0][0]
    ethics_root = q(db, "SELECT root_uid FROM threads WHERE subject = 'ccitation ethics/convention'")[0][0]
    admin_root = q(db, "SELECT root_uid FROM threads WHERE subject = 'Contacting an Admin'")[0][0]
    dec = tmp_path / "decisions"
    dec.mkdir()
    (dec / "thread_overrides.csv").write_text(
        "action,uid,target,note\n"
        f"split,{reply},,test split\n"
        f"merge,{admin_root},{ethics_root},test merge\n", encoding="utf-8")
    db2 = make(d, dec, "b.db")
    assert q(db2, "SELECT root_uid FROM threads t JOIN messages m ON m.thread_id = t.id WHERE m.uid = ?", reply) == [(reply,)]
    assert q(db2, "SELECT link_method FROM messages WHERE uid = ?", admin_root) == [("override",)]
    merged = q(db2, "SELECT t.root_uid FROM threads t JOIN messages m ON m.thread_id = t.id WHERE m.uid = ?", admin_root)[0][0]
    assert merged == ethics_root
    # The merged-away root redirects to the surviving thread.
    app = create_app(str(db2), str(d / "s2.db"))
    r = app.test_client().get(f"/t/{admin_root}")
    assert r.status_code == 301 and r.headers["Location"].endswith(f"/t/{ethics_root}")
