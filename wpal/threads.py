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
    method_of: dict[str, str]  # how the message joined its thread: header | sibling | subject | root
    threads: dict[int, Thread]
    # How the reply edge to its parent was found (item 3): message-id | wrote-line |
    # in-reply-to | subject-window | override | unlinked (a thread's first message).
    link_of: dict[str, str] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)  # old root uid -> new root uid


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
    link_of: dict[str, str] = {}
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

        link = "message-id" if method == "header" else "unlinked"
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
                            link = "wrote-line"
                            break
                if parent is None:
                    parent = prior[-1]
                    link = "in-reply-to" if method == "sibling" else "subject-window"
            t.members.append(m.uid)
            t.last_seen = max(t.last_seen, m.date)

        thread_of[m.uid] = tid
        parent_of[m.uid] = parent
        method_of[m.uid] = method
        link_of[m.uid] = link
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
        link_of[m.uid] = "unlinked"

    return ThreadResult(thread_of, parent_of, method_of, threads, link_of)


def apply_overrides(tr: ThreadResult, overrides: list[dict], when: dict) -> list[str]:
    """Apply decisions/thread_overrides.csv rows in order.

    split  UID         UID and its replies leave their thread and start their own.
    merge  UID TARGET  UID's whole thread joins TARGET's; UID's root replies to TARGET.

    `when` maps uid -> sort key (date), to keep members in order. Returns notes
    for rows that could not apply (an id no longer in the build).
    """
    problems = []
    next_tid = max(tr.threads, default=0) + 1
    for o in overrides:
        action, uid, target = o.get("action", "").strip(), o.get("uid", "").strip(), o.get("target", "").strip()
        if uid not in tr.thread_of or (action == "merge" and target not in tr.thread_of):
            problems.append(f"{action} {uid} {target}: id not in this build")
            continue
        if action == "split":
            old = tr.threads[tr.thread_of[uid]]
            if old.root == uid:
                continue
            moving, frontier = {uid}, [uid]
            while frontier:  # uid and everything that replies to it, directly or not
                p = frontier.pop()
                for m in old.members:
                    if tr.parent_of.get(m) == p and m not in moving:
                        moving.add(m)
                        frontier.append(m)
            t = Thread(next_tid, uid, old.subject_key, old.last_seen, sorted(moving, key=lambda u: when[u]))
            tr.threads[next_tid] = t
            old.members = [m for m in old.members if m not in moving]
            for m in moving:
                tr.thread_of[m] = next_tid
            tr.parent_of[uid] = None
            tr.link_of[uid] = "unlinked"
            next_tid += 1
        elif action == "merge":
            a, b = tr.thread_of[uid], tr.thread_of[target]
            if a == b:
                continue
            ta, tb = tr.threads[a], tr.threads[b]
            if when[ta.root] < when[tb.root]:
                # UID's thread is the older one: the joined thread starts at its
                # root, and TARGET's old root replies to the last earlier message.
                earlier = [m for m in ta.members if when[m] < when[tb.root]]
                tr.parent_of[tb.root], tr.link_of[tb.root] = earlier[-1], "override"
                tr.redirects[tb.root] = ta.root
                tb.root = ta.root
            else:
                tr.parent_of[ta.root], tr.link_of[ta.root] = target, "override"
                tr.redirects[ta.root] = tb.root
            for m in ta.members:
                tr.thread_of[m] = b
            tb.members = sorted(tb.members + ta.members, key=lambda u: when[u])
            del tr.threads[a]
        else:
            problems.append(f"unknown action {action!r}")
    return problems
