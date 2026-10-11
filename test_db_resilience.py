"""
Unit tests for db_resilience.py — all pure, no database or Discord needed.
Run with:  pytest test_db_resilience.py
"""

from datetime import datetime, timezone

import pytest

import db_resilience as dbr


# ---------- helpers: fake psycopg2-style exceptions, fake pool, fake clock ----------

def _make_exc(name, module="psycopg2", bases=(Exception,)):
    return type(name, bases, {"__module__": module})


OperationalError = _make_exc("OperationalError")
InterfaceError = _make_exc("InterfaceError")
IntegrityError = _make_exc("IntegrityError")
# psycopg2.errors.* classes subclass OperationalError but have their own names
DeadlockDetected = _make_exc("DeadlockDetected", module="psycopg2.errors", bases=(OperationalError,))
PoolError = _make_exc("PoolError")


class FakeConn:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"FakeConn({self.name})"


class FakePool:
    """getconn() hands out queued items in order; an Exception instance in the
    queue is raised instead. Records every putconn."""

    def __init__(self, items):
        self.items = list(items)
        self.returned = []  # (conn, close)

    def getconn(self):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def putconn(self, conn, close=False):
        self.returned.append((conn, close))


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


# ---------- clean_error ----------

def test_clean_error_collapses_newlines_and_includes_type():
    exc = OperationalError("SSL connection has been closed unexpectedly\n")
    assert dbr.clean_error(exc) == "OperationalError: SSL connection has been closed unexpectedly"


def test_clean_error_multiline_message_is_one_line():
    exc = RuntimeError("line one\n  line two\n")
    assert "\n" not in dbr.clean_error(exc)
    assert dbr.clean_error(exc) == "RuntimeError: line one line two"


def test_clean_error_empty_message_still_names_type():
    assert dbr.clean_error(KeyError()) == "KeyError"


# ---------- is_transient_db_error ----------

def test_psycopg2_operational_and_interface_errors_are_transient():
    assert dbr.is_transient_db_error(OperationalError("anything"))
    assert dbr.is_transient_db_error(InterfaceError("connection already closed"))


def test_subclass_of_operational_error_is_transient():
    assert dbr.is_transient_db_error(DeadlockDetected("deadlock detected"))


def test_integrity_error_is_not_transient():
    assert not dbr.is_transient_db_error(IntegrityError("duplicate key value violates unique constraint"))


def test_plain_logic_errors_are_not_transient():
    assert not dbr.is_transient_db_error(ValueError("bad value"))
    assert not dbr.is_transient_db_error(KeyError("user_id"))


def test_known_connection_messages_are_transient_even_without_psycopg2_class():
    assert dbr.is_transient_db_error(RuntimeError("SSL connection has been closed unexpectedly"))
    assert dbr.is_transient_db_error(RuntimeError("server closed the connection unexpectedly"))
    assert dbr.is_transient_db_error(RuntimeError("connection to server failed: timeout expired"))


def test_an_unrelated_class_named_operational_error_from_another_library_is_not_transient():
    other = _make_exc("OperationalError", module="sqlite3")
    assert not dbr.is_transient_db_error(other("database is locked"))


# ---------- IdleTracker ----------

def test_unknown_connection_needs_ping():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    assert tracker.needs_ping(FakeConn("a"))


def test_recently_used_connection_does_not_need_ping():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    tracker.mark_used(conn)
    clock.t += 14.9
    assert not tracker.needs_ping(conn)


def test_connection_idle_past_threshold_needs_ping():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    tracker.mark_used(conn)
    clock.t += 15
    assert tracker.needs_ping(conn)


def test_forget_makes_connection_need_ping_again():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    tracker.mark_used(conn)
    tracker.forget(conn)
    assert tracker.needs_ping(conn)


def test_forget_unknown_connection_is_a_noop():
    dbr.IdleTracker().forget(FakeConn("never-seen"))


# ---------- checkout_validated ----------

def _checkout(pool, ping, tracker, clock=None, **kwargs):
    clock = clock or FakeClock()
    kwargs.setdefault("max_attempts", 6)
    return dbr.checkout_validated(pool, ping, tracker, sleep=clock.sleep, clock=clock, **kwargs)


def test_recently_used_connection_is_returned_without_a_ping():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    tracker.mark_used(conn)
    pings = []
    got = _checkout(FakePool([conn]), pings.append, tracker, clock)
    assert got is conn
    assert pings == []


def test_idle_connection_that_passes_ping_is_returned():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    pings = []
    pool = FakePool([conn])
    got = _checkout(pool, pings.append, tracker, clock)
    assert got is conn
    assert pings == [conn]
    assert pool.returned == []  # nothing discarded


def test_dead_connection_is_discarded_and_next_one_used():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    dead, fresh = FakeConn("dead"), FakeConn("fresh")

    def ping(conn):
        if conn is dead:
            raise OperationalError("SSL connection has been closed unexpectedly")

    pool = FakePool([dead, fresh])
    got = _checkout(pool, ping, tracker, clock)
    assert got is fresh
    assert pool.returned == [(dead, True)]  # closed, not recycled back into the pool


def test_every_stale_connection_in_the_pool_is_discarded_before_a_fresh_one():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    dead = [FakeConn(f"dead{i}") for i in range(3)]
    fresh = FakeConn("fresh")

    def ping(conn):
        if conn in dead:
            raise InterfaceError("connection already closed")

    pool = FakePool(dead + [fresh])
    assert _checkout(pool, ping, tracker, clock) is fresh
    assert [c for c, close in pool.returned if close] == dead


def test_gives_up_with_last_error_after_max_attempts():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conns = [FakeConn(f"c{i}") for i in range(3)]

    def ping(conn):
        raise OperationalError(f"dead {conn.name}")

    with pytest.raises(OperationalError, match="dead c2"):
        _checkout(FakePool(conns), ping, tracker, clock, max_attempts=3)


def test_discarded_connection_is_forgotten_by_tracker():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    dead, fresh = FakeConn("dead"), FakeConn("fresh")
    tracker.mark_used(dead)
    clock.t += 60  # idle long enough to be pinged

    def ping(conn):
        if conn is dead:
            raise OperationalError("closed")

    _checkout(FakePool([dead, fresh]), ping, tracker, clock)
    assert tracker.needs_ping(dead)  # no stale "recently used" record left behind


def test_pool_exhaustion_waits_then_succeeds():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    conn = FakeConn("a")
    pool = FakePool([PoolError("connection pool exhausted"), PoolError("connection pool exhausted"), conn])
    got = _checkout(pool, lambda c: None, tracker, clock, exhausted_exc=PoolError, wait_seconds=5.0)
    assert got is conn
    assert len(clock.sleeps) == 2


def test_pool_exhaustion_raises_after_the_wait_budget():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    pool = FakePool([PoolError("connection pool exhausted")] * 1000)
    with pytest.raises(PoolError):
        _checkout(pool, lambda c: None, tracker, clock, exhausted_exc=PoolError,
                  wait_seconds=1.0, poll_seconds=0.25)
    assert sum(clock.sleeps) == pytest.approx(1.0, abs=0.3)


def test_other_pool_errors_propagate_immediately_without_waiting():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    pool = FakePool([OperationalError("could not connect to server")])
    with pytest.raises(OperationalError, match="could not connect"):
        _checkout(pool, lambda c: None, tracker, clock, exhausted_exc=PoolError)
    assert clock.sleeps == []


def test_exhaustion_is_not_swallowed_when_no_exhausted_exc_given():
    clock = FakeClock()
    tracker = dbr.IdleTracker(idle_seconds=15, clock=clock)
    pool = FakePool([PoolError("connection pool exhausted")])
    with pytest.raises(PoolError):
        _checkout(pool, lambda c: None, tracker, clock)


# ---------- next_job_delay ----------

def test_no_failures_uses_normal_interval():
    assert dbr.next_job_delay(1800, 0) == 1800
    assert dbr.next_job_delay(600, -1) == 600


def test_failures_retry_sooner_with_doubling_delay():
    assert [dbr.next_job_delay(3600, n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]


def test_retry_delay_never_exceeds_normal_interval():
    assert dbr.next_job_delay(100, 1) == 30
    assert dbr.next_job_delay(100, 3) == 100  # 120 capped at the normal interval
    assert dbr.next_job_delay(600, 10) == 600


def test_huge_failure_counts_do_not_overflow_or_exceed_normal():
    assert dbr.next_job_delay(1800, 10_000) == 1800


# ---------- JobHealth ----------

class _Ticker:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return datetime(2026, 10, 11, 12, 0, self.n, tzinfo=timezone.utc)


def test_unknown_job_has_no_failures():
    assert dbr.JobHealth().consecutive_failures("nope") == 0
    assert dbr.JobHealth().snapshot() == {}


def test_failure_then_success_resets_consecutive_but_keeps_totals():
    health = dbr.JobHealth(now=_Ticker())
    health.record_failure("sweep", OperationalError("SSL connection has been closed unexpectedly\n"))
    health.record_failure("sweep", OperationalError("again"))
    assert health.consecutive_failures("sweep") == 2
    health.record_success("sweep")
    assert health.consecutive_failures("sweep") == 0
    snap = health.snapshot()["sweep"]
    assert snap["total_failures"] == 2
    assert snap["total_successes"] == 1
    assert snap["last_error"] == "OperationalError: again"
    assert snap["last_success"] is not None and snap["last_failure"] is not None


def test_jobs_are_tracked_independently():
    health = dbr.JobHealth(now=_Ticker())
    health.record_failure("a", RuntimeError("boom"))
    health.record_success("b")
    assert health.consecutive_failures("a") == 1
    assert health.consecutive_failures("b") == 0


def test_snapshot_is_a_copy():
    health = dbr.JobHealth(now=_Ticker())
    health.record_success("a")
    snap = health.snapshot()
    snap["a"]["total_successes"] = 999
    assert health.snapshot()["a"]["total_successes"] == 1


def test_record_failure_accepts_plain_string():
    health = dbr.JobHealth(now=_Ticker())
    health.record_failure("a", "something odd")
    assert health.snapshot()["a"]["last_error"] == "something odd"
