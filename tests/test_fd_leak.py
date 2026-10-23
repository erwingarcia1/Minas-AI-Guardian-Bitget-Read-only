"""#79: every dashboard/portal request leaked a file descriptor.

ThreadingHTTPServer runs each connection in a fresh thread; Database hands
each thread its own SQLite connection and keeps a strong reference in
`_all_conns` so `close()` can find it. A request thread dies after one
connection, but the reference lives until process exit — so the descriptor
does too. In production the process hit its 1024-fd ceiling roughly ninety
minutes after every restart, at which point it could not open ANY socket:
not the dashboard's, and not SMTP, Telegram or the exchange API either.
Alert delivery shared the fd budget with the leak.

The tests below hammer the REAL servers through real sockets and require
the process's descriptor count to stay flat — per this issue's rule that
the check must exercise what it certifies (the healthcheck watched a URL
whose handler it never reached the failure mode of; these tests reach it).
"""
from __future__ import annotations

import http.client
import os
import threading
import time

import pytest

from sentinel.dashboard import serve_dashboard
from sentinel.portal import serve_portal, set_member_password


@pytest.fixture
def stop():
    servers = []
    yield servers.append
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _get(port: int, path: str = "/", cookie: str | None = None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Cookie": cookie} if cookie else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _signed_in_portal_cookie(db, port: int) -> str:
    """A real session, because the leak needs a DB-touching request to show:
    an anonymous portal GET renders the login page without ever opening this
    thread's connection, and a test hammering it passes with or without the
    fix — the trap CONTRIBUTING's mutation section exists for."""
    set_member_password(db, "leak@test.invalid", "pw-123456")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (label, exchange, topology, encrypted_api_key, "
            "encrypted_secret, encrypted_passphrase, alert_email, status, "
            "onboarded_at, updated_at) VALUES ('Leak', 'bitget', 'independent', "
            "'x', 'y', 'z', 'leak@test.invalid', 'active', '2026-01-01', '2026-01-01')"
        )
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = "email=leak%40test.invalid&password=pw-123456"
        conn.request("POST", "/login", body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        set_cookie = resp.getheader("Set-Cookie") or ""
        assert "minas_portal=" in set_cookie
        return "minas_portal=" + set_cookie.split("minas_portal=", 1)[1].split(";", 1)[0]
    finally:
        conn.close()


def _settled_fd_count(baseline: int, slack: int, deadline_s: float = 5.0) -> int:
    """The fd count once request threads have finished dying.

    A handler thread can outlive its HTTP response by a few milliseconds, so
    the count is polled rather than read once — otherwise the test would be
    flaky in exactly the way that teaches people to ignore it."""
    end = time.monotonic() + deadline_s
    count = _open_fds()
    while count > baseline + slack and time.monotonic() < end:
        time.sleep(0.05)
        count = _open_fds()
    return count


REQUESTS = 200
# Generous slack for interpreter incidentals (a lingering thread, an import
# side effect). The leak this pins was one fd per request: 200 requests met
# with a slack of 25 cannot hide it.
SLACK = 25


def test_dashboard_requests_do_not_leak_file_descriptors(db, cipher, stop):
    from tests.test_member_account_grants import _sign_in_ops

    server = serve_dashboard(db, cipher, 0)
    stop(server)
    port = server.server_address[1]
    cookie = _sign_in_ops(db, cipher, port)  # the gate admits nobody else (#103)

    for _ in range(10):  # warm-up: let steady-state allocations happen
        _get(port, cookie=cookie)
    baseline = _settled_fd_count(_open_fds(), 0, deadline_s=1.0)

    for _ in range(REQUESTS):
        assert _get(port, cookie=cookie) == 200  # the REAL root through the REAL handler

    assert _settled_fd_count(baseline, SLACK) <= baseline + SLACK


def test_portal_requests_do_not_leak_file_descriptors(db, stop):
    server = serve_portal(db, 0)
    stop(server)
    port = server.server_address[1]
    cookie = _signed_in_portal_cookie(db, port)

    for _ in range(10):
        _get(port, cookie=cookie)
    baseline = _settled_fd_count(_open_fds(), 0, deadline_s=1.0)

    for _ in range(REQUESTS):
        assert _get(port, cookie=cookie) == 200  # signed in: touches the DB

    assert _settled_fd_count(baseline, SLACK) <= baseline + SLACK


def test_dead_request_threads_leave_no_connection_behind(db):
    """The mechanism, pinned below the HTTP layer: a short-lived thread that
    queries and is then released must not grow the connection pool."""
    def worker():
        with db.cursor() as cur:
            cur.execute("SELECT count(*) c FROM accounts")
            cur.fetchone()
        db.release_thread_connection()

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The creating thread's connection is the only one that should remain.
    assert len(db._all_conns) <= 1


def test_a_released_thread_reconnects_on_next_use(db):
    """Release must not wedge the calling thread: the next query opens a
    fresh connection transparently."""
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM accounts")
    db.release_thread_connection()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) c FROM accounts")
        assert cur.fetchone()["c"] == 0


def test_a_delivery_after_the_answer_runs_on_its_own_thread_and_leaves_no_connection(db):
    """What a request owes - a new-device notice, a closing note - is sent
    after the answer on a thread of its own (sentinel/outbox.py): one more
    short-lived thread that queries the database, so one more that must
    release its connection, or every sign-in and acknowledgement costs a
    descriptor the way every request once did. Outside a request the
    delivery is made at once, on the caller's thread, as before."""
    from sentinel.outbox import answering, later, wait_until_sent

    ran = []

    def deliver():
        with db.cursor() as cur:
            cur.execute("SELECT count(*) c FROM accounts")
        ran.append(threading.current_thread())

    later(deliver)
    assert ran == [threading.current_thread()], "outside a request: at once, here"

    pool = len(db._all_conns)
    with answering(db):
        later(deliver)
        assert len(ran) == 1, "inside a request: not before the answer"
    assert wait_until_sent(5), "the delivery never finished"
    assert len(ran) == 2 and ran[1] is not threading.current_thread()
    assert len(db._all_conns) == pool, "the delivery thread kept its connection"
