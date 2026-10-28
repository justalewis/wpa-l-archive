"""Item 6: genre rules, the evaluation table, the search filter and the job view."""

import os
import shutil
import sqlite3

import pytest

os.environ["WPAL_NO_AUTOAPP"] = "1"
os.environ["WPAL_PUBLIC_MIN_POSTS"] = "1"

from wpal import build  # noqa: E402
from wpal.app import create_app  # noqa: E402
from wpal.stages.genre import rank_type, rule_label  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.parametrize("subject,body,root,expected", [
    ("CFP: Writing Center Journal special issue", "", True, "cfp"),
    ("Assistant Professor position at Example University", "The department invites applications for", True, "job"),
    ("Re: job market", "I think the market is worse than people admit.", False, None),
    ("In memoriam: a colleague", "", True, "tribute"),
    ("set wpa-l nomail", "", True, "administrative"),
    ("Newest issue of College English now online", "", True, "announcement"),
    ("rubrics for portfolios", "Does anyone have a rubric they would share?", True, "query"),
    ("Register now", "Are you a new WPA? There is still time to register.", True, "announcement"),
    ("of interest", "Check this out: an article on adjunct pay.", True, "discussion"),
])
def test_rules(subject, body, root, expected):
    assert rule_label(subject, body, root)[0] == expected


@pytest.mark.parametrize("text,kind", [
    ("Director of the Writing Center, tenure-track", "writing center director"),
    ("Director of First-Year Writing", "WPA"),
    ("Full-time lecturer in composition", "non-tenure-line"),
    ("Tenure-track Assistant Professor of Rhetoric", "tenure-line"),
])
def test_job_rank_types(text, kind):
    assert rank_type(text) == kind


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    d = tmp_path_factory.mktemp("genre")
    raw = d / "raw"
    raw.mkdir()
    for src, dest in {"era1993.txt": "1993-03.txt", "era2011_pair.txt": "2011-07.txt"}.items():
        shutil.copy(os.path.join(FIX, src), raw / dest)
    dec = d / "decisions"
    dec.mkdir()
    # First build to learn a uid; then hand-label it and rebuild.
    args = ["--raw", str(raw), "--salt", str(d / "s"), "--decisions", str(dec), "--report", str(d / "r.md"), "--workers", "1"]
    build.main([*args, "--db", str(d / "a.db")])
    uid = sqlite3.connect(d / "a.db").execute("SELECT uid FROM messages WHERE subject = 'placement'").fetchone()[0]
    (dec / "genre_labels.csv").write_text(f"uid,label,coder\n{uid},query,test coder\n", encoding="utf-8")
    build.main([*args, "--db", str(d / "b.db")])
    a = create_app(str(d / "b.db"), str(d / "state.db"))
    a.db_path = d / "b.db"
    a.hand_uid = uid
    return a


def test_hand_label_wins_and_is_evaluated(app):
    con = sqlite3.connect(app.db_path)
    assert con.execute("SELECT genre, genre_source FROM messages WHERE uid = ?", (app.hand_uid,)).fetchone() == \
        ("query", "hand:test coder")
    assert con.execute("SELECT support FROM genre_eval WHERE label = 'query'").fetchone()[0] == 1
    page = app.test_client().get("/genres").data.decode()
    assert "test coder" in page and "Precision" in page


def test_genre_filter_and_jobs_page(app):
    c = app.test_client()
    assert "placement" in c.get("/search?genre=query&start=1993&end=1993").data.decode()
    assert c.get("/jobs").status_code == 200
    con = sqlite3.connect(app.db_path)
    replies = con.execute("SELECT DISTINCT genre FROM messages WHERE parent_uid IS NOT NULL").fetchall()
    assert {g for (g,) in replies} <= {"discussion", "debate", "cfp", "job", "announcement", "tribute", "administrative"}
