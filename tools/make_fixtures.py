"""Cut test fixtures from the raw logs, with addresses and sender names replaced.

    python tools/make_fixtures.py

Keeps each block's real header layout, MIME structure and encodings (what the
tests exercise) but swaps every email address for a fake example.edu one and
every sender's display name, wherever it appears in the block, for a made-up
name. Base64 runs are cut to two lines. Message text otherwise stays as posted;
review the fixtures before this repository is made public.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wpal.parse import split_blocks  # noqa: E402

RAW = Path("data/raw")
OUT = Path("tests/fixtures")
FAKE_NAMES = ["Avery Quill", "Rowan Ledger", "Morgan Folio", "Casey Margin", "Jordan Gloss",
              "Riley Stanza", "Harper Codex", "Emerson Vellum", "Quinn Rubric", "Sage Draft"]
ADDR = re.compile(rb"[\w.+%'-]+@+[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")
PHONE = re.compile(rb"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\b\d{3} \d\n?\d \d{4}\b")
# Names and places the sender swap misses: people named in the body, signatures.
MANUAL = [(b"jfindura=\n@bergen.edu", b"person9=\n@example.edu"),
          (b"Ann Merle Feldman", b"Harper Codex"), (b"Feldman", b"Codex"),
          (b"bonnie lenore kyburz", b"sage draft"), (b"Bonnie Kyburz", b"Sage Draft"),
          (b"research.uvsc.edu/kyburz", b"example.edu/~draft"), (b"Linda", b"Lee"),
          (b"llarry", b"aq"), (b"Doug", b"Dale"), (b"Ed White", b"Ellis Rowe"),
          (b"Stan Sulkes", b"Sam Lark"), (b"Sulkes", b"Lark"), (b"Eric", b"Erin"),
          (b"Univ of South Alabama", b"Example State Univ"), (b"Mobile, AL 36688", b"Anytown, ST 00000"),
          (b"John", b"Jamie"), (b"The Cerullo Learning Assistance Center", b"The Learning Center"),
          (b"Cerullo", b"Example"), (b"Bergen Community College", b"Example Community College"),
          (b"Room L-125", b"Room 100"), (b"400 Paramus Ro=\nad", b"1 Main Ro=\nad"),
          (b"400 Paramus Road", b"1 Main Road"), (b"Paramus, NJ 07652", b"Anytown, ST 00000"),
          (b"Paramus Ro", b"Main Ro"), (b"Paramus", b"Anytown"), (b"Santa Barbara, CA 93106-3010", b"Anytown, ST 00000")]
B64_RUN = re.compile(rb"((?:^[A-Za-z0-9+/=]{60,}\n){2})(?:^[A-Za-z0-9+/=]{20,}\n)+", re.M)

SELECT = {
    "era1993.txt": ("1993/1993-03.txt", lambda i, b: i < 4),
    "era1995_irt.txt": ("1995/1995-01.txt", lambda i, b: b"In-Reply-To:  Message of" in b and len(b) < 4000),
    "era1999_qp.txt": ("1999/1999-06.txt",
                       lambda i, b: b"quoted-printable" in b[:1200].lower() and b"=3D" not in b and b"=\n" in b and len(b) < 5000),
    "era2006_multipart.txt": ("2006/2006-04.txt", lambda i, b: b"ms-tnef" in b and b"Benchmark WAC" in b),
    "era2011_pair.txt": ("2011/2011-07.txt", lambda i, b: i in (2, 3)),
    "era2020_reply.txt": ("2020/2020-03.txt", lambda i, b: b"Re: Contacting an Admin" in b[:600]
                          or (b"Subject:      Contacting an Admin" in b[:600])),
}


def pseudonymize(blocks: list[bytes]) -> bytes:
    addrs: dict[bytes, bytes] = {}
    names: dict[bytes, bytes] = {}
    for b in blocks:
        m = re.search(rb"^From:\s+\"?([^<\"\n]+?)\"?\s*<", b, re.M)
        if m and m.group(1).strip() not in names:
            names[m.group(1).strip()] = FAKE_NAMES[len(names) % len(FAKE_NAMES)].encode()

    def fake_addr(m):
        a = m.group(0).lower()
        if a.startswith(b"wpa-l@"):
            return m.group(0)
        if a not in addrs:
            addrs[a] = b"person%d@example.edu" % (len(addrs) + 1)
        return addrs[a]

    out = []
    for b in blocks:
        b = B64_RUN.sub(lambda m: m.group(1), b)
        for real, fake in names.items():
            b = b.replace(real, fake)
            parts = real.replace(b",", b" ").split()
            if len(parts) >= 2:
                last = max(parts, key=len)
                b = re.sub(rb"\b" + re.escape(last) + rb"\b", fake.split()[-1], b)
        for real, fake in MANUAL:
            b = b.replace(real, fake)
        b = ADDR.sub(fake_addr, b)
        b = PHONE.sub(b"555-0100", b)
        out.append(b"=" * 73 + b"\n" + b.rstrip(b"\n") + b"\n")
    return b"".join(out)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (src, pick) in SELECT.items():
        blocks = split_blocks((RAW / src).read_bytes())
        chosen = [b for i, b in enumerate(blocks) if pick(i, b)]
        if name in ("era1995_irt.txt", "era1999_qp.txt", "era2006_multipart.txt"):
            chosen = chosen[:1]
        if not chosen:
            print("no match for", name)
            continue
        (OUT / name).write_bytes(pseudonymize(chosen))
        print(name, len(chosen), "blocks")


if __name__ == "__main__":
    main()
