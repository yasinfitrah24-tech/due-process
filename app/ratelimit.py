"""Limits for a public form: reports per IP, and how many cases may wait for a sandbox."""
import threading
import time
from collections import defaultdict, deque

REPORTS_PER_WINDOW = 10
WINDOW_S = 600
MAX_WAITING = 20
APPEALS_PER_WINDOW = 5

_lock = threading.Lock()
_seen = defaultdict(deque)


def allow(ip, now=None, limit=None):
    now = time.monotonic() if now is None else now
    limit = limit or REPORTS_PER_WINDOW
    with _lock:
        times = _seen[ip]
        while times and now - times[0] >= WINDOW_S:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(now)
        return True


def reset():
    with _lock:
        _seen.clear()
