"""Rebuild threads across the whole archive.

Headers come first. From about 2010 the log keeps each message's Message-ID,
so a reply's In-Reply-To or References points at a message we hold. Before
that the log drops Message-ID, and In-Reply-To names a message the log never
recorded; two replies that share one still belong together, so we use it to
join siblings.

Everything else falls back on the subject: strip Re:/Fwd: and list tags,
normalize, and join the most recent thread with that subject if it was active
within the window. A message that is not marked as a reply needs a tighter
window, so a new "question" post a month later starts its own thread.

Within a thread, a reply's parent is the message it names by header, else the
latest earlier message whose author matches the reply's "X wrote:" line, else
the latest earlier message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from .parse import Message, normalize_subject

REPLY_WINDOW = timedelta(days=30)
FRESH_WINDOW = timedelta(days=3)
# Subjects too generic to join on without a header link.
GENERIC = {"", "question", "help", "query", "request", "thanks", "thank you",
           "hello", "hi", "info", "information", "test", "no subject", "inquiry",
           "job announcement", "job posting", "job", "position announcement", "position",
           "announcement", "cfp", "call for papers", "call for proposals", "call for submissions",
           "new book", "conference announcement", "query", "survey", "request for help"}


@dataclass
class Thread:
    tid: int
    root: str
    subject_key: str
    last_seen: object  # datetime
    members: list[str] = field(default_factory=list)


@dataclass
class ThreadResult:
    thread_of: dict[str, int]
    parent_of: dict[str, str | None]
    method_of: dict[str, str]  # header | sibling | subject | root
    threads: dict[int, Thread]


def _name_key(name: str) -> str:
    parts = name.replace(",", " ").lower().split()
    return " ".join(sorted(parts))


def build_threads(messages: list[Message]) -> ThreadResult:
    order = sorted(
        (m for m in messages if m.date is not None),
        key=lambda m: (m.date, m.source_file, m.ordinal),
    )
    undated = [m for m in messages if m.date is None]

    by_msgid: dict[str, str] = {}
    by_irt: dict[str, str] = {}
    open_by_subject: dict[str, int] = {}
    thread_of: dict[str, int] = {}
    parent_of: dict[str, str | None] = {}
    method_of: dict[str, str] = {}
    threads: dict[int, Thread] = {}
    author: dict[str, str] = {}
    subject_key: dict[str, str] = {}
    next_tid = 1

    def start(m: Message, key: str) -> int:
        nonlocal next_tid
        t = Thread(next_tid, m.uid, key, m.date, [m.uid])
        threads[next_tid] = t
        next_tid += 1
        return t.tid

    for m in order:
        author[m.uid] = _name_key(m.from_name)
        key = normalize_subject(m.subject)
        subject_key[m.uid] = key
        tid = None
        parent = None
        method = "root"

        for h in [m.in_reply_to_hash, *reversed(m.reference_hashes)]:
            if h and h in by_msgid:
                parent = by_msgid[h]
                tid = thread_of[parent]
                method = "header"
                break
        # Some clients reused one In-Reply-To across unrelated replies, so a shared
        # value joins siblings only when their subjects also agree.
        if tid is None and m.in_reply_to_hash and m.in_reply_to_hash in by_irt:
            sib = by_irt[m.in_reply_to_hash]
            if subject_key[sib] == key or not key:
                tid = thread_of[sib]
                method = "sibling"
        if tid is None and key not in GENERIC and key in open_by_subject:
            cand = threads[open_by_subject[key]]
            gap = m.date - cand.last_seen
            if gap <= (REPLY_WINDOW if m.is_reply else FRESH_WINDOW):
                tid = cand.tid
                method = "subject"

        if tid is None:
            tid = start(m, key)
        else:
            t = threads[tid]
            if parent is None:
                wanted = m.attribution
                prior = t.members
                if wanted:
                    wk = _name_key(wanted)
                    for uid in reversed(prior):
                        if author.get(uid) and (author[uid] == wk or wk in author[uid] or author[uid] in wk):
                            parent = uid
                            break
                if parent is None:
                    parent = prior[-1]
            t.members.append(m.uid)
            t.last_seen = max(t.last_seen, m.date)

        thread_of[m.uid] = tid
        parent_of[m.uid] = parent
        method_of[m.uid] = method
        if m.message_id_hash:
            by_msgid.setdefault(m.message_id_hash, m.uid)
        if m.in_reply_to_hash:
            by_irt.setdefault(m.in_reply_to_hash, m.uid)
        if key not in GENERIC:
            open_by_subject[key] = tid

    for m in undated:
        tid = start(m, normalize_subject(m.subject))
        thread_of[m.uid] = tid
        parent_of[m.uid] = None
        method_of[m.uid] = "root"

    return ThreadResult(thread_of, parent_of, method_of, threads)
