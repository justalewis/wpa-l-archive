"""Items 11, 7 and 8: events overlay, participation, flashpoints."""

import os
import shutil
import sqlite3

import pytest

os.environ["WPAL_NO_AUTOAPP"] = "1"
os.environ["WPAL_PUBLIC_MIN_POSTS"] = "1"

from wpal import build  # noqa: E402
from wpal.app import create_app  # noqa: E402
from wpal.stages.network import gini  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    d = tmp_path_factory.mktemp("ov")
    raw = d / "raw"
    raw.mkdir()
    for src, dest in {"era1993.txt": "1993-03.txt", "era2011_pair.txt": "2011-07.txt", "era2020_reply.txt": "2020-03.txt"}.items():
        shutil.copy(os.path.join(FIX, src), raw / dest)
    dec = d / "decisions"
    dec.mkdir()
    (dec / "events.yaml").write_text(
        '- {date: 1993-03, label: "CCCC, San Diego", category: cccc, source: "/search?q=CCCC"}\n'
        '- {date: 2011-07-01, label: "Board change", category: cwpa}\n', encoding="utf-8")
    build.main(["--raw", str(raw), "--db", str(d / "a.db"), "--salt", str(d / "s"), "--decisions", str(dec),
                "--report", str(d / "r.md"), "--workers", "1"])
    a = create_app(str(d / "a.db"), str(d / "state.db"))
    a.db_path, a.state_path = d / "a.db", d / "state.db"
    return a


def test_events_load_and_overlay(app):
    c = app.test_client()
    page = c.get("/events").data.decode()
    assert "CCCC, San Diego" in page and "Board change" in page
    assert 'class="event-line"' not in c.get("/timeline").data.decode()
    tl = c.get("/timeline?events=1").data.decode()
    assert tl.count('class="event-line"') == 2 and 'data-tip-title="CCCC, San Diego"' in tl
    tr = c.get("/trends?terms=placement&events=1").data.decode()
    assert "event-line" in tr


def test_participation_respects_withdrawal(app):
    c = app.test_client()
    con = sqlite3.connect(app.db_path)
    era = "2008–2012"
    page = c.get(f"/participation?era={era}").data.decode()
    assert page.count("<circle") >= 1 and "Who stayed?" in page
    named = con.execute("SELECT p.person_id, p.display_name FROM persons p JOIN person_era_stats s "
                        "ON s.person_id = p.person_id WHERE s.era = ? AND s.degree > 0", (era,)).fetchall()
    assert named
    pid, name = named[0]
    assert f'data-tip-title="{name}"' in page
    key = con.execute("SELECT poster_key FROM messages WHERE person_id = ? LIMIT 1", (pid,)).fetchone()[0]
    st = sqlite3.connect(app.state_path)
    st.execute("INSERT INTO suppression (kind, key, label) VALUES ('poster', ?, 'test')", (key,))
    st.commit()
    st.close()
    page = c.get(f"/participation?era={era}").data.decode()
    assert f'data-tip-title="{name}"' not in page and "withdrawn" in page


def test_concentration_and_cohorts_computed(app):
    con = sqlite3.connect(app.db_path)
    assert con.execute("SELECT COUNT(*) FROM concentration").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM cohorts WHERE first_year = 1993").fetchone()[0] == 3


def test_flashpoints_page(app):
    c = app.test_client()
    assert c.get("/flashpoints").status_code == 200
    assert c.get("/flashpoints?kind=week").status_code == 200


def test_gini():
    assert gini([1, 1, 1, 1]) == 0
    assert 0.7 < gini([0, 0, 0, 10]) <= 0.75
