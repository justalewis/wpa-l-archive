"""Build-stage tests: quote/signature splitting and poster identity."""

import pytest

from wpal.stages.fresh import split
from wpal.stages.identity import normalize_name


def test_interleaved_quotes_keep_the_replies():
    body = "On Mon, Ann Smith wrote:\n> first point\nI agree.\n> second point\nNot this one."
    fresh, quoted, sig = split(body)
    assert fresh == "I agree.\nNot this one."
    assert "first point" in quoted and "Ann Smith wrote" in quoted


def test_outlook_top_post_is_cut_at_the_header_block():
    body = ("Will do.\n\n________________________________\n\nFrom: List on behalf of Bo Li\n"
            "Sent: Sat 4/1/2006\nTo: [address removed]\nSubject: Re: x\n\nUnmarked earlier message.")
    fresh, quoted, _ = split(body)
    assert fresh == "Will do."
    assert "Unmarked earlier message" in quoted


def test_original_message_rule_and_groupwise():
    assert split("Yes.\n-----Original Message-----\nFrom: x\nold")[0] == "Yes."
    assert split("Thanks.\n>>> [address removed] 03/31/06 3:54 PM >>>\nold text")[0] == "Thanks."


def test_signature_after_rule_and_trailing_block():
    fresh, _, sig = split("Point made.\n\n--\nJo Doe\nWriting Program\nExample University")
    assert fresh == "Point made." and "Example University" in sig
    fresh, _, sig = split("A longer point here.\n\nBest,\nJo\n\nJo Doe, Ph.D.\nDirector of Composition\nExample State University\n555-0100")
    assert "Example State University" in sig and "A longer point here." in fresh and "Best," in fresh


def test_prose_ending_is_not_taken_for_a_signature():
    body = "The program review asked about the university budget and the college's plans."
    assert split(body) == (body, "", "")


@pytest.mark.parametrize("raw,expected", [
    ("Bergmann, Linda", "linda bergmann"),
    ("Dr. Linda S. Bergmann, Ph.D.", "linda bergmann"),
    ("Linda Bergmann", "linda bergmann"),
    ("LINDA BERGMANN", "linda bergmann"),
    ("Ann Merle Feldman, Director of English Composition", "ann feldman"),
    ("José García", "jose garcia"),
    ("Unnamed poster", ""),
    ("Sue", "sue"),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected
