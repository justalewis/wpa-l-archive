"""Citation strings for one list message, in MLA 9 and APA 7.

MLA 9 treats a post to a mailing list like an email or forum post: author,
the subject in quotation marks, the list as container, the date, the URL.
APA 7 uses its online-forum pattern, with the bracketed description.
"""

from __future__ import annotations

import re
from datetime import datetime

MONTHS_MLA = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
DEGREES = re.compile(r",?\s*\b(ph\.?\s?d\.?|ed\.?\s?d\.?|m\.?f\.?a\.?|m\.?a\.?|jr\.?|sr\.?)\s*$", re.I)


def split_name(name: str) -> tuple[str, str] | None:
    """(last, first) from a name as posted, or None when it isn't a person's name."""
    n = DEGREES.sub("", name.strip().strip('"')).strip(" ,")
    if not n or n == "Unnamed poster" or "@" in n:
        return None
    if "," in n:
        last, first = [p.strip() for p in n.split(",", 1)]
        return (last, first) if last and first else None
    parts = n.split()
    if len(parts) < 2:
        return None
    return parts[-1], " ".join(parts[:-1])


def mla_author(name: str) -> str:
    s = split_name(name)
    return f"{s[0]}, {s[1]}" if s else name


def apa_author(name: str) -> str:
    s = split_name(name)
    if not s:
        return name
    initials = " ".join(f"{p[0]}." for p in re.split(r"[\s.]+", s[1]) if p and p[0].isalpha())
    return f"{s[0]}, {initials}" if initials else s[0]


def _end(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def citations(from_name: str, subject: str, sent_at: str | None, year: int, month: int, url: str) -> list[tuple[str, str]]:
    subject = subject or "[No subject]"
    if sent_at:
        d = datetime.fromisoformat(sent_at)
        mla_date = f"{d.day} {MONTHS_MLA[d.month - 1]} {d.year}"
        apa_date = f"{d.year}, {d.strftime('%B')} {d.day}"
    else:
        mla_date = f"{MONTHS_MLA[month - 1]} {year}"
        apa_date = f"{year}, {datetime(year, month, 1).strftime('%B')}"
    mla = f"{_end(mla_author(from_name))} “{_end(subject)}” WPA-L, {mla_date}, {url}."
    apa = f"{_end(apa_author(from_name))} ({apa_date}). {subject} [Online forum post]. WPA-L. {url}"
    return [("MLA 9", mla), ("APA 7", apa)]
