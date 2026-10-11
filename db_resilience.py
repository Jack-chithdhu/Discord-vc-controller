"""
db_resilience.py — pure helpers for surviving stale or dropped database connections.

Same philosophy as vc_stats.py and reservation_logic.py: zero dependency on
discord.py, Flask or psycopg2, so the logic can be unit tested (see
test_db_resilience.py) with fakes instead of a live database. app.py supplies
the real connection pool and the real "ping" query and calls into this.

Why this exists: the hosted Postgres (or something between it and the bot)
closes connections that sit idle for a few minutes. The pool kept handing out
those dead connections, so the first query after a quiet period failed with
"SSL connection has been closed unexpectedly". The periodic jobs (every
10/30/60 minutes) always hit a dead connection that way, while voice logging
only survived because it has its own retry. The fix is to prove a connection
is alive *before* handing it out, and to retry failed jobs sooner than their
normal interval.
"""

import threading
import time
from datetime import datetime, timezone


# ---------- error classification ----------

# Messages libpq/psycopg2 use when the server or network drops a connection.
_TRANSIENT_MARKERS = (
    "ssl connection has been closed",
    "server closed the connection",
    "connection already closed",
    "connection not open",
    "terminating connection",
    "could not receive data from server",
    "could not send data to server",
    "connection reset",
    "timeout expired",
    "decryption failed or bad record mac",
    "ssl syscall error",
    "the database system is starting up",
    "the database system is shutting down",
)
_TRANSIENT_CLASS_NAMES = {"OperationalError", "InterfaceError"}


def clean_error(exc) -> str:
    """One-line 'ErrorType: message' for logs. psycopg2 messages end with a
    newline (which produced a stray blank log line) and can span several lines."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def is_transient_db_error(exc) -> bool:
    """True for connection-level failures that are worth retrying (a dropped
    or closed connection). False for genuine SQL/logic errors, where retrying
    would just fail the same way again."""
    for klass in type(exc).__mro__:
        if klass.__module__.split(".")[0] == "psycopg2" and klass.__name__ in _TRANSIENT_CLASS_NAMES:
            return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


# ---------- validated connection checkout ----------

class IdleTracker:
    """Remembers when each pooled connection was last used. A connection used
    moments ago is trusted as-is; one that has sat idle for `idle_seconds` or
    more (or has never been seen) gets pinged before it is handed out. This
    keeps the extra round-trip off busy paths like back-to-back voice events."""

    def __init__(self, idle_seconds: float = 15.0, clock=time.monotonic):
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._last_used = {}
        self._lock = threading.Lock()

    def needs_ping(self, conn) -> bool:
        with self._lock:
            last = self._last_used.get(id(conn))
        return last is None or (self._clock() - last) >= self._idle_seconds

    def mark_used(self, conn):
        with self._lock:
            self._last_used[id(conn)] = self._clock()

    def forget(self, conn):
        with self._lock:
            self._last_used.pop(id(conn), None)


def checkout_validated(pool, ping, tracker, *, max_attempts, exhausted_exc=(),
                       wait_seconds: float = 5.0, poll_seconds: float = 0.05,
                       sleep=time.sleep, clock=time.monotonic):
    """
    Gets a connection from `pool` that is known to be alive.

    - A connection that needs checking (see IdleTracker) is pinged; if the ping
      raises, that connection is discarded (closed, never returned to the pool)
      and the next one is tried — the pool opens a fresh connection when it has
      no idle ones left. After `max_attempts` discards the last error is raised.
    - If the pool is exhausted (`exhausted_exc`, e.g. psycopg2's PoolError),
      waits up to `wait_seconds` for a connection to be returned instead of
      failing immediately, then re-raises.
    - Any other error from the pool itself (e.g. the database is down and a new
      connection can't be opened) propagates straight away.
    """
    deadline = clock() + wait_seconds
    discarded = 0
    while True:
        try:
            conn = pool.getconn()
        except exhausted_exc:
            if clock() >= deadline:
                raise
            sleep(poll_seconds)
            continue

        if tracker.needs_ping(conn):
            try:
                ping(conn)
            except Exception as exc:
                tracker.forget(conn)
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
                discarded += 1
                if discarded >= max_attempts:
                    raise exc
                continue
        return conn


# ---------- background job retry timing + health ----------

def next_job_delay(normal_seconds: float, consecutive_failures: int, retry_base: float = 30.0) -> float:
    """How long a periodic job should wait before its next run. Normally the
    job's usual interval; after failures, retry sooner (30s, 60s, 120s, ...)
    but never later than the usual interval. Without this, one failed run
    meant waiting the whole interval (up to an hour) to try again."""
    if consecutive_failures <= 0:
        return normal_seconds
    exponent = min(consecutive_failures, 20) - 1
    return min(normal_seconds, retry_base * (2 ** exponent))


class JobHealth:
    """Thread-safe record of how each named background job last did. Purely
    in memory (resets on restart) — meant to be read by diagnostics such as
    Doctor Mode or an admin health panel."""

    def __init__(self, now=None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._jobs = {}
        self._lock = threading.Lock()

    def _entry(self, name):
        return self._jobs.setdefault(name, {
            "last_success": None, "last_failure": None, "last_error": None,
            "consecutive_failures": 0, "total_successes": 0, "total_failures": 0,
        })

    def record_success(self, name):
        with self._lock:
            entry = self._entry(name)
            entry["last_success"] = self._now()
            entry["consecutive_failures"] = 0
            entry["total_successes"] += 1

    def record_failure(self, name, error):
        with self._lock:
            entry = self._entry(name)
            entry["last_failure"] = self._now()
            entry["last_error"] = clean_error(error) if isinstance(error, BaseException) else str(error)
            entry["consecutive_failures"] += 1
            entry["total_failures"] += 1

    def consecutive_failures(self, name) -> int:
        with self._lock:
            return self._jobs.get(name, {}).get("consecutive_failures", 0)

    def snapshot(self) -> dict:
        with self._lock:
            return {name: dict(entry) for name, entry in self._jobs.items()}
