"""Item 4: normalized trends, quoted-text exclusion, and click-through."""

import os
import shutil

import pytest

os.environ["WPAL_NO_AUTOAPP"] = "1"
os.environ["WPAL_PUBLIC_MIN_POSTS"] = "1"

from wpal import build  # noqa: E402
from wpal.app import create_app  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    d = tmp_path_factory.mktemp("trends")
    raw = d / "raw"
    raw.mkdir()
    shutil.copy(os.path.join(FIX, "era2020_reply.txt"), raw / "2020-03.txt")
    shutil.copy(os.path.join(FIX, "era1993.txt"), raw / "1993-03.txt")
    build.main(["--raw", str(raw), "--db", str(d / "a.db"), "--salt", str(d / "s"),
                "--report", str(d / "r.md"), "--workers", "1"])
    a = create_app(str(d / "a.db"), str(d / "state.db"))
    a.config["TESTING"] = True
    return a


def test_quoted_text_counts_once_with_own_words(app):
    # "doctoral" is in the root message and again in the reply's quoted text.
    with app.app_context():
        assert sum(app.trend_counts("doctoral", False, False).values()) == 2
        assert sum(app.trend_counts("doctoral", True, False).values()) == 1
        assert sum(app.trend_counts("doctoral", True, True).values()) == 1


def test_rate_is_per_thousand_messages(app):
    c = app.test_client()
    page = c.get("/trends?terms=doctoral&unit=year").data.decode()
    # 2 matching of 2 messages in 2020 -> 1,000 per 1,000.
    assert "1000.00" in page
    raw = c.get("/trends?terms=doctoral&unit=year&measure=raw").data.decode()
    assert "messages per 1,000" not in raw.split("Y axis:")[1][:40]


def test_points_link_to_the_right_messages(app):
    c = app.test_client()
    page = c.get("/trends?terms=doctoral&unit=month").data.decode()
    assert "/search?q=doctoral&amp;start=2020&amp;end=2020&amp;month=3" in page
    hit = c.get("/search?q=doctoral&start=2020&end=2020&month=3").data.decode()
    assert "Contacting an Admin" in hit
    miss = c.get("/search?q=doctoral&start=2020&end=2020&month=4").data.decode()
    assert "No messages matched" in miss


def test_six_terms_at_most_and_empty_state(app):
    c = app.test_client()
    page = c.get("/trends?terms=a1,b2,c3,d4,e5,f6,g7,h8").data.decode()
    assert page.count('class="series s') == 6
    assert "Enter one or more terms" in c.get("/trends").data.decode()


def test_search_histogram_has_a_normalized_view(app):
    page = app.test_client().get("/search?q=doctoral&norm=1").data.decode()
    assert "per 1,000 messages posted" in page
