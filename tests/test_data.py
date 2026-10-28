"""Item 2: corpora, exports and the API, and that withdrawals reach all of them."""

import csv
import io
import json
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


def make_db(d, name):
    raw = d / "raw"
    if not raw.exists():
        raw.mkdir()
        for src, dest in NAMES.items():
            shutil.copy(os.path.join(FIX, src), raw / dest)
    db = d / name
    build.main(["--raw", str(raw), "--db", str(db), "--salt", str(d / "salt"),
                "--report", str(d / f"{name}.md"), "--workers", "1"])
    return db


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    return d, make_db(d, "a.db")


@pytest.fixture
def client(built):
    d, db = built
    state = d / "state.db"
    if state.exists():
        state.unlink()
    app = create_app(str(db), str(state))
    app.config["TESTING"] = True
    c = app.test_client()
    c.state = state
    return c


def csrf(client):
    page = client.get("/opt-out").data.decode()
    return page.split('name="csrf" value="')[1].split('"')[0]


def agree(client):
    client.post("/data", data={"csrf": csrf(client), "agree": "yes"})


def save(client, **filters):
    r = client.post("/corpus", data={"csrf": csrf(client), "name": "test corpus", **filters})
    assert r.status_code == 302, r.data[:300]
    return r.headers["Location"].rsplit("/", 1)[1]


def test_export_needs_terms_then_streams_fields(client):
    r = client.get("/export?q=placement&format=csv")
    assert r.status_code == 302 and "/data" in r.headers["Location"]
    agree(client)
    r = client.get("/export?q=placement&format=csv")
    rows = list(csv.DictReader(io.StringIO(r.data.decode())))
    assert rows and set(rows[0]) == {"message_id", "thread_id", "poster_id", "poster_name", "date_utc", "year",
                                      "month", "subject", "body", "body_own_words", "permalink"}
    assert all("@example.edu" not in r["body"] for r in rows)
    assert r.headers["X-Robots-Tag"].startswith("noindex")


def test_pseudonymized_export_is_stable_and_hides_names(client):
    agree(client)
    a = [json.loads(l) for l in client.get("/export?from=Quill&format=jsonl&pseudo=1").data.decode().splitlines()]
    b = [json.loads(l) for l in client.get("/export?from=Quill&format=jsonl&pseudo=1").data.decode().splitlines()]
    assert a and [r["poster_id"] for r in a] == [r["poster_id"] for r in b]
    assert all(r["poster_name"].startswith("P") and "Quill" not in r["poster_name"] for r in a)
    assert all(r["poster_name"] == r["poster_id"] for r in a)


def test_corpus_keeps_its_ids_across_a_rebuild(client, built, tmp_path):
    d, _ = built
    cid = save(client, q="placement OR welcome OR admin")
    first = client.get(f"/api/v1/corpus/{cid}").get_json()
    assert first["n_saved"] >= 3
    # Rebuild from the same logs into a fresh database; point a new app at it
    # with the same state.db (where corpora live).
    db2 = make_db(d, "b.db")
    app2 = create_app(str(db2), str(client.state))
    second = app2.test_client().get(f"/api/v1/corpus/{cid}").get_json()
    assert second["message_ids"] == first["message_ids"]
    assert [m["id"] for m in second["results"]] == [m["id"] for m in first["results"]]


def test_withdrawal_reaches_corpora_exports_and_api(client, built):
    _, db = built
    cid = save(client, q="placement OR welcome OR admin")
    agree(client)
    con = sqlite3.connect(db)
    uid, key = con.execute("SELECT uid, poster_key FROM messages WHERE from_name = 'Rowan Ledger' LIMIT 1").fetchone()
    pid = con.execute("SELECT person_id FROM messages WHERE uid = ?", (uid,)).fetchone()[0]
    st = sqlite3.connect(client.state)
    st.execute("INSERT INTO suppression (kind, key, label) VALUES ('poster', ?, 'test')", (key,))
    st.commit()
    st.close()
    assert client.get(f"/api/v1/messages/{uid}").status_code == 404
    assert client.get(f"/api/v1/posters/{pid}").status_code == 404
    body = client.get(f"/api/v1/corpus/{cid}").get_json()
    assert uid not in [m["id"] for m in body["results"]]
    assert "Rowan Ledger" not in client.get(f"/corpus/{cid}").data.decode()
    rows = list(csv.DictReader(io.StringIO(client.get(f"/export?corpus={cid}&format=csv").data.decode())))
    assert rows and uid not in {r["message_id"] for r in rows}
    assert "Rowan Ledger" not in {r["poster_name"] for r in rows}
    # Their name can still appear inside other people's messages (quoted
    # text, mentions): an open question recorded on /ethics, not a leak here.
    hits = client.get("/api/v1/search?q=welcome").get_json()["results"]
    assert uid not in {m["id"] for m in hits} and "Rowan Ledger" not in {m["poster_name"] for m in hits}


def test_api_shapes_and_rate_limit(client, built):
    _, db = built
    key = sqlite3.connect(db).execute("SELECT root_uid FROM threads WHERE n_messages > 1 LIMIT 1").fetchone()[0]
    t = client.get(f"/api/v1/threads/{key}").get_json()
    assert t["n_messages"] >= 2 and "citation" in t["messages"][0]
    s = client.get("/api/v1/search?q=placement").get_json()
    assert s["total"] >= 1 and s["page_size"] == 50
    codes = [client.get("/api/v1/").status_code for _ in range(70)]
    assert 429 in codes


def test_corpus_needs_a_name_and_a_token(client):
    assert client.post("/corpus", data={"name": "x", "q": "placement"}).status_code == 400
    r = client.post("/corpus", data={"csrf": csrf(client), "name": "", "q": "placement"})
    assert r.status_code == 302 and "/corpus/" not in r.headers["Location"]
