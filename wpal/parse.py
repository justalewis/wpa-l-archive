"""Split WPA-L LISTSERV notebook logs into messages.

Every monthly file in the Internet Archive deposit is a LISTSERV "notebook"
log: messages separated by a line of 73 '=' signs, each opening with a
column-aligned header block (Date, Reply-To, Sender, From, Subject, and from
the late 1990s on MIME headers). Bodies are stored as sent, so attachments
arrive inline as base64, which is most of the deposit's 3.5 GB.

The source text is untrusted data. Nothing here evaluates it; we decode it,
take the plain text out, and drop every email address before anything is
stored. Addresses survive only in memory, long enough to key a salted poster
hash and to recover a display name for posters whose From line had none.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import hmac
import html
import quopri
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

SEPARATOR = re.compile(rb"^={73}\r?\n(?=Date:)", re.M)

# Local part @ domain. The domain may be a bare BITNET node (C509379@MIZZOU1)
# or dotted; percent-routed addresses (user%host@relay) match as one token.
_DOMAIN = r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
ADDRESS = re.compile(
    r"(?:\"[^\"\n]{1,60}\"|(?<![\w.+%'-])[\w.+%'-]+)@+" + _DOMAIN
    # Older mail systems wrote "user @ HOST" or "user @host.edu". The bare-host
    # form needs spaces on both sides so "@CCCC" handles and "@ ASU" survive.
    + r"|(?<![\w.+%'-])[\w.+%'-]+(?: @ [A-Z][A-Z0-9-]{3,}\b| ?@ ?[A-Za-z][\w-]*(?:\.[\w-]+)+)",
    re.UNICODE,
)
# An address wrapped in <>, (), [], or a mailto: link is noise once the address
# is gone, so the wrapper goes with it.
WRAPPED_ADDRESS = re.compile(
    r"\s*(?:<\s*(?:mailto:)?|\(\s*(?:mailto:)?|\[\s*mailto:)"
    r"[\w.+%'-]+@+" + _DOMAIN + r"\s*[>)\]]"
)
MAILTO = re.compile(r"mailto:\s*", re.I)
ADDRESS_MARK = "[address removed]"

REPLY_PREFIX = re.compile(
    r"^\s*(?:(?:re|aw|sv|fw|fwd|antw|ynt)\s*(?:\[\d+\])?\s*[:;]\s*|\[wpa-l\]\s*)+",
    re.I,
)
FWD_SUFFIX = re.compile(r"\s*\((?:fwd|fw|forward)\)\s*$", re.I)
ATTRIBUTION_LINE = re.compile(r"^>?\s*(?P<lead>[^\n>]{2,200}?)\s*wrote:\s*$", re.M)
CLOCK = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?(?:\s*[A-Z]{3,4}\b)?,?")


@dataclass
class Message:
    source_file: str
    ordinal: int  # position within the monthly file, from 0
    uid: str  # stable public id
    date: datetime | None
    date_raw: str
    from_name: str
    subject: str
    body: str
    poster_key: str  # salted hash of the sender address; "" when none
    message_id_hash: str = ""
    in_reply_to_hash: str = ""
    reference_hashes: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)  # content types only
    is_reply: bool = False
    problems: list[str] = field(default_factory=list)
    attribution: str | None = None  # name in the reply's "X wrote:" line
    # Transient: used during the build to recover names, never stored.
    _address: str = ""


def split_blocks(raw: bytes) -> list[bytes]:
    """Return the raw bytes of each message in a monthly log."""
    starts = [m.end() for m in SEPARATOR.finditer(raw)]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw)
        chunk = raw[start:end]
        if i + 1 < len(starts):
            # Trim the separator line that belongs to the next message.
            chunk = re.sub(rb"\r?\n?={73}\r?\n$", b"\n", chunk)
        blocks.append(chunk)
    return blocks


def scrub_addresses(text: str) -> str:
    text = WRAPPED_ADDRESS.sub("", text)
    text = MAILTO.sub("", text)
    return ADDRESS.sub(ADDRESS_MARK, text)


def decode_words(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def clean_header(value: str | None) -> str:
    return re.sub(r"\s+", " ", decode_words(value)).strip()


def normalize_subject(subject: str) -> str:
    s = subject
    for _ in range(5):
        s2 = FWD_SUFFIX.sub("", REPLY_PREFIX.sub("", s))
        if s2 == s:
            break
        s = s2
    s = re.sub(r"[\s\W_]+", " ", s.lower()).strip()
    return s


def _hash(*parts: bytes | str, n: int = 16) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:n]


def poster_key(address: str, salt: bytes) -> str:
    if not address:
        return ""
    norm = address.strip().lower()
    return hmac.new(salt, norm.encode(), hashlib.sha256).hexdigest()[:20]


def _msgid_hashes(value: str | None) -> list[str]:
    # LISTSERV-era "Message of <date> from <address>" names a sender, not a message.
    if not value or value.strip().lower().startswith("message of"):
        return []
    return [_hash(m.lower()) for m in re.findall(r"<([^<>\s]+@[^<>\s]+)>", value)]


def _split_head(block: bytes) -> tuple[bytes, bytes]:
    """LISTSERV ends the header block with an empty or single-space line."""
    lines = block.split(b"\n")
    for i, line in enumerate(lines):
        if not line.strip():
            return b"\n".join(lines[:i]), b"\n".join(lines[i + 1 :])
    return block, b""


def _decode_bytes(data: bytes, charset: str | None) -> str:
    for cs in filter(None, [charset, "utf-8", "cp1252"]):
        try:
            return data.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", markup)
    markup = re.sub(r"(?i)<blockquote[^>]*>", "\n> ", markup)
    markup = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(markup)
    return re.sub(r"\n{3,}", "\n\n", text)


def _undo_double_qp(text: str) -> str:
    """Some 2000s clients quoted-printable-encoded text that was already QP.

    One decode leaves '=3D' and '=20' sequences and soft '=' line ends behind.
    Decode again only when those leftovers are plentiful.
    """
    leftovers = len(re.findall(r"=3D|=20\n|=\n", text))
    if leftovers >= 3:
        try:
            return quopri.decodestring(text.encode("utf-8")).decode("utf-8", "replace")
        except Exception:
            return text
    return text


def _is_attachment(part) -> bool:
    disp = (part.get("Content-Disposition") or "").lower()
    return disp.startswith("attachment") or bool(part.get_filename())


def extract_text(msg) -> tuple[str, list[str], list[str]]:
    """Return (text, attachment content types, problems)."""
    problems: list[str] = []
    attachments: list[str] = []
    plains: list[str] = []
    htmls: list[str] = []
    if not msg.is_multipart():
        parts = [msg]
    else:
        parts = [p for p in msg.walk() if not p.is_multipart()]
    for part in parts:
        ctype = part.get_content_type()
        if ctype.startswith("text/") and not _is_attachment(part):
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                problems.append("payload-decode")
                payload = (part.get_payload() or "").encode("latin-1", "replace")
            text = _decode_bytes(payload, part.get_content_charset())
            if ctype == "text/html":
                htmls.append(_html_to_text(text))
            elif ctype in ("text/plain", "text/enriched", "text/richtext") or not msg.is_multipart():
                plains.append(text)
            else:
                attachments.append(ctype)
        elif ctype.startswith("message/"):
            continue  # walk() descends into the enclosed message's parts
        else:
            attachments.append(ctype)
    if plains:
        body = "\n\n".join(plains)
    elif htmls:
        body = "\n\n".join(htmls)
        problems.append("html-only")
    else:
        body = ""
        problems.append("no-text-part")
    body = _undo_double_qp(body.replace("\r\n", "\n").replace("\r", "\n"))
    # LISTSERV wrote blank lines as a single space; trailing blanks carry nothing.
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    return body.strip("\n"), attachments, problems


def parse_block(block: bytes, source_file: str, ordinal: int, salt: bytes) -> Message:
    head, body = _split_head(block)
    msg = email.message_from_bytes(head + b"\n\n" + body, policy=email.policy.compat32)
    problems: list[str] = []

    date_raw = clean_header(msg.get("Date"))
    date = None
    try:
        date = parsedate_to_datetime(date_raw)
        if date.tzinfo is None:
            problems.append("date-no-zone")
            date = date.replace(tzinfo=timezone.utc)
        date = date.astimezone(timezone.utc)
    except Exception:
        problems.append("date-unparsed")

    raw_from = clean_header(msg.get("From"))
    addrs = getaddresses([raw_from]) if raw_from else []
    name, address = (addrs[0] if addrs else ("", ""))
    if not address and ADDRESS.search(raw_from):
        address = ADDRESS.search(raw_from).group(0)
    name = scrub_addresses(name).replace(ADDRESS_MARK, "").strip(" \"'")
    if not name and not address:
        problems.append("no-from")

    subject = scrub_addresses(clean_header(msg.get("Subject")))

    text, attachments, body_problems = extract_text(msg)
    problems += body_problems

    mid = _msgid_hashes(msg.get("Message-ID"))
    irt = _msgid_hashes(msg.get("In-Reply-To"))
    refs = _msgid_hashes(msg.get("References"))
    is_reply = bool(
        msg.get("In-Reply-To") or msg.get("References") or REPLY_PREFIX.match(subject)
    )

    # A stable id from raw bytes, so later parser changes do not move permalinks.
    if mid:
        uid = mid[0][:12]
    else:
        uid = _hash(head, body[:4096], n=12)

    return Message(
        source_file=source_file,
        ordinal=ordinal,
        uid=uid,
        date=date,
        date_raw=date_raw,
        from_name=name,
        subject=subject,
        body=scrub_addresses(text),
        poster_key=poster_key(address, salt),
        message_id_hash=mid[0] if mid else "",
        in_reply_to_hash=irt[0] if irt else "",
        reference_hashes=refs,
        attachments=attachments,
        is_reply=is_reply,
        problems=problems,
        attribution=attributed_name(scrub_addresses(text)),
        _address=address.lower(),
    )


def parse_file(path: str, source_file: str, salt: bytes) -> list[Message]:
    with open(path, "rb") as fh:
        raw = fh.read()
    return [parse_block(b, source_file, i, salt) for i, b in enumerate(split_blocks(raw))]


def attributed_name(body: str) -> str | None:
    """The name in the first 'X wrote:' line of a reply, if any.

    Handles "Ann Smith wrote:", "On 3/1/20 4:10 PM, Ann Smith wrote:" and
    Gmail's "On Sun, Mar 1, 2020, 4:10 PM Ann Smith wrote:".
    """
    m = ATTRIBUTION_LINE.search(body)
    if not m:
        return None
    s = m.group("lead").replace(ADDRESS_MARK, "").strip()
    if s.startswith("On "):
        times = list(CLOCK.finditer(s))
        s = s[times[-1].end():] if times else s.rsplit(",", 1)[-1]
    s = s.strip(" ,\"'")
    if not (2 <= len(s) <= 60) or re.search(r"\d", s) or len(s.split()) > 6:
        return None
    return s
