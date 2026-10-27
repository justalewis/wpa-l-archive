"""Reader tests: noindex everywhere, suppression honored by every view, opt-out form."""

import os
import shutil
import sqlite3

import pytest

os.environ["WPAL_NO_AUTOAPP"] = "1"

from wpal import build  # noqa: E402
from wpal.app import create_app, fts_query  # noqa: E402
from wpal.build import name_key  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
NAMES = {"era1993.txt": "1993-03.txt", "era2011_pair.txt": "2011-07.txt", "era2020_reply.txt": "2020-03.txt"}


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    d = tmp_path_factory.mktemp("wpal")
    raw = d / "raw"
    raw.mkdir()
    for src, dest in NAMES.items():
        shutil.copy(os.path.join(FIX, src), raw / dest)
    db = d / "archive.db"
    build.main(["--raw", str(raw), "--db", str(db), "--salt", str(d / "salt"),
                "--report", str(d / "coverage.md"), "--workers", "1"])
    return db, d / "requests.db"


@pytest.fixture
def client(paths):
    db, req = paths
    if req.exists():
        req.unlink()
    app = create_app(str(db), str(req))
    app.config["TESTING"] = True
    return app.test_client()


def one(db, sql, *args):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def suppress(req, kind, key):
    con = sqlite3.connect(req)
    con.execute("INSERT INTO suppression (kind, key, label) VALUES (?, ?, 'test')", (kind, key))
    con.commit()
    con.close()


def test_no_addresses_in_database(paths):
    db, _ = paths
    n = one(db, "SELECT COUNT(*) FROM messages WHERE body LIKE '%@example.edu%' "
                "OR subject LIKE '%@example.edu%' OR from_name LIKE '%@%'")[0]
    assert n == 0


def test_noindex_everywhere(client, paths):
    db, _ = paths
    uid = one(db, "SELECT uid FROM messages LIMIT 1")[0]
    tid = one(db, "SELECT thread_id FROM messages LIMIT 1")[0]
    for url in ["/", "/browse/1993", "/browse/1993/3", f"/thread/{tid}", f"/m/{uid}",
                "/search?q=placement", "/timeline", "/about", "/opt-out", "/nope"]:
        r = client.get(url)
        assert r.headers["X-Robots-Tag"].startswith("noindex"), url
        if r.mimetype == "text/html":
            assert b'name="robots" content="noindex' in r.data, url
    r = client.get("/robots.txt")
    assert r.data == b"User-agent: *\nDisallow: /\n"


def test_pages_render(client, paths):
    db, _ = paths
    assert b"Avery Quill" in client.get("/browse/1993/3").data
    assert client.get("/search?q=placement").status_code == 200
    assert b"<mark>" in client.get("/search?q=placement").data
    assert client.get('/search?q="unclosed').status_code == 200
    assert client.get("/browse/1993/13").status_code == 404


def test_suppression_by_name_hides_poster_everywhere(client, paths):
    db, req = paths
    uid, tid = one(db, "SELECT uid, thread_id FROM messages WHERE from_name = 'Avery Quill' AND year = 1993")
    before_month = client.get("/browse/1993/3").data
    assert b"Avery Quill" in before_month
    suppress(req, "name", name_key("Avery Quill"))

    assert client.get(f"/m/{uid}").status_code == 410
    t = client.get(f"/thread/{tid}")
    assert b"Avery Quill" not in t.data and b"withdrawn" in t.data
    assert b"Avery Quill" not in client.get("/browse/1993/3").data
    assert b"Avery Quill" not in client.get("/search?q=citing").data
    assert b"Avery Quill" not in client.get("/search?from=Quill").data
    total = one(db, "SELECT COUNT(*) FROM messages")[0]
    hidden = one(db, "SELECT COUNT(*) FROM messages WHERE name_key = ?", name_key("Avery Quill"))[0]
    assert f"{total - hidden:,} messages".encode() in client.get("/").data


def test_suppression_by_poster_key(client, paths):
    db, req = paths
    uid, key = one(db, "SELECT uid, poster_key FROM messages WHERE from_name = 'Rowan Ledger' AND year = 2011")
    assert client.get(f"/m/{uid}").status_code == 200
    suppress(req, "poster", key)
    assert client.get(f"/m/{uid}").status_code == 410


def test_optout_records_request(client, paths):
    _, req = paths
    page = client.get("/opt-out").data.decode()
    token = page.split('name="csrf" value="')[1].split('"')[0]
    r = client.post("/opt-out", data={"csrf": token, "name_as_posted": "Avery Quill",
                                      "contact": "me@example.org", "relationship": "self"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/opt-out/received")
    row = one(req, "SELECT name_as_posted, status FROM optout_requests")
    assert row == ("Avery Quill", "new")
    # Nothing is suppressed until the request is reviewed.
    assert one(req, "SELECT COUNT(*) FROM suppression")[0] == 0


def test_optout_rejects_missing_fields_and_bad_token(client, paths):
    _, req = paths
    r = client.post("/opt-out", data={"csrf": "wrong", "name_as_posted": "", "contact": ""})
    assert r.status_code == 200 and b"expired" in r.data
    assert one(req, "SELECT COUNT(*) FROM optout_requests")[0] == 0


@pytest.mark.parametrize("raw,expected", [
    ("placement", '"placement"'),
    ('"dual credit" OR concurrent', '"dual credit" OR "concurrent"'),
    ("assess*", '"assess"*'),
    ("NEAR( ) ^col:x", '"NEAR" "colx"'),
    ("OR", '""'),
])
def test_fts_query(raw, expected):
    assert fts_query(raw) == expected
