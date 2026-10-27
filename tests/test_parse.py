"""Parser tests on real blocks from each era (addresses and senders swapped out)."""

from pathlib import Path

import pytest

from wpal.parse import (
    ADDRESS_MARK,
    normalize_subject,
    parse_file,
    scrub_addresses,
    split_blocks,
)
from wpal.threads import build_threads

FIX = Path(__file__).parent / "fixtures"
SALT = b"test-salt"


def load(name):
    return parse_file(str(FIX / name), name, SALT)


def no_addresses(m):
    for text in (m.body, m.subject, m.from_name):
        assert "@example.edu" not in text
        assert "@ASU" not in text.upper() or "WPA-L@" not in text.upper()


@pytest.mark.parametrize("name,count", [
    ("era1993.txt", 4), ("era1995_irt.txt", 1), ("era1999_qp.txt", 1),
    ("era2006_multipart.txt", 1), ("era2011_pair.txt", 2), ("era2020_reply.txt", 2),
])
def test_split_counts(name, count):
    assert len(split_blocks((FIX / name).read_bytes())) == count


def test_separator_inside_body_does_not_split():
    raw = (b"=" * 73 + b"\nDate: Mon, 1 Mar 1993 12:00:00 -0800\nFrom: A <a@x.edu>\nSubject: s\n \n"
           b"body\n" + b"=" * 73 + b"\nnot a header\n")
    assert len(split_blocks(raw)) == 1


def test_1993_listserv_headers():
    msgs = load("era1993.txt")
    first, unnamed = msgs[0], msgs[1]
    assert first.from_name == "Avery Quill"
    assert first.date.isoformat() == "1993-03-01T20:34:35+00:00"
    assert first.subject == "ccitation ethics/convention"
    assert first.body.startswith("Thanks to all who responded")
    # The single-space line ending the header block is not a header continuation.
    assert "Reply-To" not in first.body
    # An address-only From line leaves no name, but still keys a poster.
    assert unnamed.from_name == ""
    assert unnamed.poster_key and unnamed.poster_key == msgs[3].poster_key
    assert unnamed._address == "person2@example.edu"
    assert unnamed.is_reply and not first.is_reply
    for m in msgs:
        no_addresses(m)
        assert " \n" not in m.body


def test_1995_multiline_from_and_message_of_reply():
    (m,) = load("era1995_irt.txt")
    assert m.from_name.startswith("Harper Codex")
    assert m.is_reply
    # "Message of ... from <address>" must not become a message-id link.
    assert m.in_reply_to_hash == ""
    assert m.problems == ["date-no-zone"] or m.date is not None
    assert ADDRESS_MARK in m.body
    no_addresses(m)


def test_1999_quoted_printable():
    (m,) = load("era1999_qp.txt")
    assert "sticky issue.  Indeed, my present school" in m.body
    assert "=\n" not in m.body and "=20" not in m.body
    no_addresses(m)


def test_2006_multipart_attachment_and_double_qp():
    (m,) = load("era2006_multipart.txt")
    assert m.attachments == ["application/ms-tnef"]
    assert "eJ8+IiAP" not in m.body  # base64 attachment never reaches the text
    assert "=3D" not in m.body
    assert "short list of successful WAC/WID programs" in " ".join(m.body.split())
    assert ">>> " + ADDRESS_MARK in m.body
    no_addresses(m)


def test_2011_message_id_threading():
    parent, reply = load("era2011_pair.txt")
    assert parent.message_id_hash and reply.in_reply_to_hash == parent.message_id_hash
    assert parent.uid == parent.message_id_hash[:12]
    tr = build_threads([parent, reply])
    assert tr.thread_of[reply.uid] == tr.thread_of[parent.uid]
    assert tr.parent_of[reply.uid] == parent.uid
    assert tr.method_of[reply.uid] == "header"


def test_2020_reply_plain_text_preferred_and_attribution():
    root, reply = load("era2020_reply.txt")
    assert "<div" not in reply.body
    assert reply.attribution == "Avery Quill"
    assert "Avery Quill wrote:" in reply.body
    for m in (root, reply):
        no_addresses(m)
    tr = build_threads([root, reply])
    assert tr.parent_of[reply.uid] == root.uid


def test_subject_fallback_threads_1993():
    msgs = load("era1993.txt")
    tr = build_threads(msgs)
    ethics, ethics_re, placement, placement_re = msgs
    assert tr.thread_of[ethics_re.uid] == tr.thread_of[ethics.uid]
    assert tr.method_of[ethics_re.uid] == "subject"
    assert tr.thread_of[placement_re.uid] == tr.thread_of[placement.uid]
    assert tr.thread_of[placement.uid] != tr.thread_of[ethics.uid]


def test_generic_subjects_do_not_join_without_headers():
    a, b = load("era1993.txt")[2:4]
    a.subject, b.subject = "Job Announcement", "Job Announcement"
    b.is_reply = False
    tr = build_threads([a, b])
    assert tr.thread_of[a.uid] != tr.thread_of[b.uid]


def test_uid_is_stable_and_salt_only_touches_poster_key():
    a = parse_file(str(FIX / "era1993.txt"), "x", b"one")
    b = parse_file(str(FIX / "era1993.txt"), "x", b"two")
    assert [m.uid for m in a] == [m.uid for m in b]
    assert a[0].poster_key != b[0].poster_key


@pytest.mark.parametrize("raw,expected", [
    ("Re: Re: placement", "placement"),
    ("RE: [WPA-L] Fwd: Placement (fwd)", "placement"),
    ("Re[2]: Writing studies major", "writing studies major"),
    ("AW: question", "question"),
])
def test_normalize_subject(raw, expected):
    assert normalize_subject(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("write to jdoe@uni.edu today", "write to [address removed] today"),
    ("Ann Smith <ann@x.edu> wrote:", "Ann Smith wrote:"),
    ("(mailto:a.b@c.org)", ""),
    ("RNORMAN@@ASNUAH.ASN.NET", ADDRESS_MARK),
    ("To: MAILER @ UCVAX", "To: " + ADDRESS_MARK),
    ("C509379@MIZZOU1 said", ADDRESS_MARK + " said"),
    ("follow @CCCC on twitter", "follow @CCCC on twitter"),
    ("meet @ 3pm", "meet @ 3pm"),
    ("I am @ ASU now", "I am @ ASU now"),
    ("<https://nam04.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwpacouncil.org%2F&data=02%7C01%7Cjo.doe%40UNI.EDU%7Cabc&sdata=x&reserved=0>",
     "<https://wpacouncil.org/>"),
    ("data=02%7C01%7Cjo.doe%40UNI.EDU%7Cabc", "data=02%7C01" + ADDRESS_MARK + "%7Cabc"),
    ("ages 20-40 and 1993-40s", "ages 20-40 and 1993-40s"),
    ("write nick.c at gmail dot com", "write " + ADDRESS_MARK),
    ("jdoe [at] eng [dot] uni\n[dot] edu.", ADDRESS_MARK + "."),
    ("look at the site dot com bubble", "look at the site dot com bubble"),
])
def test_scrub_addresses(raw, expected):
    assert scrub_addresses(raw) == expected


@pytest.mark.parametrize("raw", [
    "data%3D02%257C01%257Cjo.doe%2540UNI.EDU%257C5",       # encoded twice
    "data-3D02-257C01-257CJo.Doe-2540UNI.EDU-257Cac",      # Proofpoint v2
    "x*7Cjo.doe*40uni.edu*7C",                              # URLDefense v3
    '<a href="mailto:a@b.edu%22%20jo.doe@uni.edu%3c/a%3e">',  # glued to encoding
    "Germantown, MD 20874jo.doe@bellatlantic.net",           # glued to text
])
def test_scrub_wrapped_link_addresses(raw):
    out = scrub_addresses(raw)
    assert ADDRESS_MARK in out and "doe" not in out.lower()
