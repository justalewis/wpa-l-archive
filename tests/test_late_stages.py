"""Items 10, 9 and 5: institution matching, citation matching, topic stage guards."""

import json
import sqlite3
from pathlib import Path

from wpal.stages import Context
from wpal.stages import citations, institutions, topics
from wpal.stages.citations import spearman


def test_carnegie_groups_and_name_normalizing():
    assert institutions.carnegie_type(15) == "Doctoral: very high research"
    assert institutions.carnegie_type(3) == "Associate's (two-year)"
    assert institutions.carnegie_type(-2) == "Not classified"
    assert institutions.norm("The Ohio State University") == "ohio state university"
    assert institutions.norm("St. Cloud State Univ.") == "saint cloud state university"


def make_db(tmp_path, bodies):
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute("CREATE TABLE messages (uid TEXT, year INTEGER, body TEXT, body_fresh TEXT, from_name TEXT)")
    db.executemany("INSERT INTO messages VALUES (?,?,?,?,?)", bodies)
    return db


def test_citation_matching(tmp_path):
    ref = tmp_path / "ref" / "pinakes"
    ref.mkdir(parents=True)
    works = [
        {"id": 1, "doi": "10.1234/ccc.2004.1", "title": "Made Not Only in Words: Composition in a New Key", "authors": "Kathleen Blake Yancey",
         "pub_date": "2004-12-01", "journal": "CCC", "internal_cited_by_count": 50},
        {"id": 2, "doi": None, "title": "The Erasure of the Sentence", "authors": "Robert Connors", "pub_date": "2000-09-01",
         "journal": "CCC", "internal_cited_by_count": 20},
        {"id": 3, "doi": None, "title": "Another Article Entirely Here", "authors": "Robert Connors", "pub_date": "1997-01-01",
         "journal": "CE", "internal_cited_by_count": 5},
        {"id": 4, "doi": None, "title": "Third Paper By Connors Is This", "authors": "Robert Connors", "pub_date": "1998-01-01",
         "journal": "CE", "internal_cited_by_count": 5},
    ]
    (ref / "works.jsonl").write_text("\n".join(json.dumps(w) for w in works), encoding="utf-8")
    db = make_db(tmp_path, [
        ("a", 2005, "", "See doi 10.1234/ccc.2004.1 for the argument.", "Jo Doe"),
        ("b", 2006, "", 'Everyone should reread "Made Not Only in Words" this term.', "Jo Doe"),
        ("c", 2007, "", "As Connors (2000) argued, the sentence vanished. Robert Connors said so.", "Jo Doe"),
        ("d", 2008, "> quoted 10.1234/ccc.2004.1", "", "Jo Doe"),
    ])
    dec = tmp_path / "decisions"
    dec.mkdir()
    citations.run(db, Context(decisions=dec, reference=tmp_path / "ref"))
    rows = db.execute("SELECT uid, kind, work_id, status FROM mentions ORDER BY uid").fetchall()
    assert ("a", "doi", 1, "accepted") in rows
    assert ("b", "title", 1, "accepted") in rows
    assert ("c", "author-year", 2, "review") in rows
    assert not any(r[0] == "d" for r in rows)  # quoted text is not the poster's own words
    assert db.execute("SELECT n_messages, first_year FROM cited_works WHERE work_id = 1").fetchone() == (2, 2005)
    assert db.execute("SELECT name FROM scholar_mentions WHERE uid = 'c'").fetchone() == ("Robert Connors",)


def test_spearman():
    assert round(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 6) == 1.0
    assert round(spearman([1, 2, 3, 4], [40, 30, 20, 10]), 6) == -1.0
    assert spearman([1, 2], [1, 2]) is None


def test_topics_skip_without_config_or_enough_documents(tmp_path):
    db = sqlite3.connect(tmp_path / "t.db")
    db.executescript("CREATE TABLE threads (id, root_uid, subject, year, month); CREATE TABLE messages (thread_id, body_fresh, sent_at);")
    ctx = Context(decisions=tmp_path, reference=tmp_path)
    topics.run(db, ctx)
    assert "no " in ctx.log[-1]
    (tmp_path / "topics.toml").write_text(Path("decisions/topics.toml").read_text(encoding="utf-8"), encoding="utf-8")
    topics.run(db, ctx)
    assert "at least 500" in ctx.log[-1] or "skipped" in ctx.log[-1]
