"""Rate limits.

Reads (the API) are limited in memory: one gunicorn process, so a per-process
sliding window is the whole picture. Writes (saved corpora, thread flags) are
limited through state.db's write_log, so a restart does not reset them.
Clients are identified by a keyed hash of their IP; the IP is never stored.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import threading
import time

from flask import current_app, request

_hits: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_lock = threading.Lock()


def client_id() -> str:
    ip = (request.headers.get("Fly-Client-IP") or request.headers.get("CF-Connecting-IP")
          or request.remote_addr or "")
    return hmac.new(current_app.secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]


def allow_read(bucket: str, per_minute: int) -> bool:
    key = f"{bucket}:{client_id()}"
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            return False
        q.append(now)
        return True


def allow_write(state_con, action: str, per_hour: int) -> bool:
    cid = client_id()
    n = state_con.execute(
        "SELECT COUNT(*) FROM write_log WHERE ip_hash = ? AND action = ? AND at > datetime('now', '-1 hour')",
        (cid, action)).fetchone()[0]
    if n >= per_hour:
        return False
    state_con.execute("INSERT INTO write_log (ip_hash, action) VALUES (?, ?)", (cid, action))
    state_con.execute("DELETE FROM write_log WHERE at < datetime('now', '-2 days')")
    return True
