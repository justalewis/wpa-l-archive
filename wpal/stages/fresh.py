"""Split a message body into the poster's own words, quoted text, and signature.

Feeds the "exclude quoted text" option in search and trends (item 4), the
affiliation extractor (item 1), and the topic, genre and citation stages,
which should count what a poster wrote rather than what they quoted.

Three quoting habits appear in the list:
- '>' quotes, sometimes interleaved with the poster's replies;
- Outlook-style top posting, where an "Original Message" rule or a
  From:/Sent:/Subject: block heads an unmarked copy of the earlier message;
- GroupWise ">>> name date >>>" heads, and forwarded-message banners.

The first kind is removed line by line, so interleaved replies survive. The
others mark the point after which nothing is the poster's own, so the text is
cut there. A signature is the block after a "--" line, or failing that a
short trailing block that looks like a sign-off (titles, institutions, phone
numbers, URLs). The heuristics are deliberately cautious: when unsure, text
stays in the fresh body.
"""

from __future__ import annotations

import re

HARD_MARKERS = [
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*Begin forwarded message:\s*$", re.I),
    re.compile(r"^\s*>>>\s.*>>>\s*$"),
    re.compile(r"^\s*_{10,}\s*$"),  # Outlook's rule, when a From: block follows
]
HEADER_LINE = re.compile(r"^\s*\*?(From|Sent|Date|To|Cc|Subject)\s*:\*?", re.I)
ATTRIBUTION = re.compile(r"^.{0,200}\bwrote:\s*$|^\s*Quoting .{1,80}:\s*$", re.I)
SIG_RULE = re.compile(r"^--\s*$|^-- $")
SIG_HINT = re.compile(
    r"\b(universit|college|institute|department|dept\.?|program|school|center|centre|professor|"
    r"director|coordinator|chair|lecturer|instructor|ph\.?d|associate|assistant)\b"
    r"|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}|https?://|www\.", re.I)


def _outlook_block(lines: list[str], i: int) -> bool:
    """True when line i starts a From:/Sent:/To:/Subject: header block."""
    if not re.match(r"^\s*\*?From\s*:", lines[i], re.I):
        return False
    window = lines[i + 1:i + 6]
    return sum(1 for l in window if HEADER_LINE.match(l)) >= 2


def split(body: str) -> tuple[str, str, str]:
    """(fresh, quoted, signature) for one message body."""
    lines = body.split("\n")
    cut = len(lines)
    for i, line in enumerate(lines):
        if any(p.match(line) for p in HARD_MARKERS[:4]) or _outlook_block(lines, i):
            cut = i
            break
        if HARD_MARKERS[4].match(line):
            j = next((k for k in range(i + 1, min(i + 4, len(lines))) if lines[k].strip()), None)
            if j is not None and _outlook_block(lines, j):
                cut = i
                break
    own, quoted = [], lines[cut:]
    head = lines[:cut]
    for i, line in enumerate(head):
        if line.lstrip().startswith(">"):
            quoted.append(line)
            continue
        if ATTRIBUTION.match(line):
            nxt = next((l for l in head[i + 1:] if l.strip()), "")
            if nxt.lstrip().startswith(">") or i >= len(head) - 2:
                quoted.append(line)
                continue
        own.append(line)

    signature: list[str] = []
    for i, line in enumerate(own):
        if SIG_RULE.match(line):
            signature, own = own[i + 1:], own[:i]
            break
    else:
        signature, own = _trailing_signature(own)

    fresh = "\n".join(own).strip("\n")
    return fresh, "\n".join(quoted).strip("\n"), "\n".join(signature).strip("\n")


def run(db, ctx) -> None:
    from . import add_column
    add_column(db, "messages", "body_fresh", "TEXT")
    add_column(db, "messages", "signature", "TEXT")
    rows = db.execute("SELECT id, body FROM messages").fetchall()
    out = []
    for mid, body in rows:
        fresh, _quoted, sig = split(body)
        out.append((fresh, sig, mid))
    db.executemany("UPDATE messages SET body_fresh = ?, signature = ? WHERE id = ?", out)
    db.executescript("""
        DROP TABLE IF EXISTS fresh_fts;
        CREATE VIRTUAL TABLE fresh_fts USING fts5(
            subject, body_fresh, content='messages', content_rowid='id',
            tokenize='porter unicode61 remove_diacritics 2');
        INSERT INTO fresh_fts(fresh_fts) VALUES ('rebuild');
        INSERT INTO fresh_fts(fresh_fts) VALUES ('optimize');
    """)
    total = sum(len(b) for _, b in rows) or 1
    kept = sum(len(f) for f, _, _ in out)
    ctx.note(f"fresh: {len(rows):,} bodies; own words are {kept / total:.0%} of body text")


def _trailing_signature(own: list[str]) -> tuple[list[str], list[str]]:
    """A short closing block with signature cues, taken from the end."""
    end = len(own)
    while end and not own[end - 1].strip():
        end -= 1
    start = end
    while start and own[start - 1].strip():
        start -= 1
    block = own[start:end]
    if not (2 <= len(block) <= 8):
        return [], own
    if any(len(l) > 80 for l in block) or not any(SIG_HINT.search(l) for l in block):
        return [], own
    return block, own[:start]
