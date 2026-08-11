from __future__ import annotations

import socket

import pytest

from sentinel.dashboard import serve_dashboard
from sentinel.metrics import Metrics
from sentinel.portal import serve_portal


@pytest.fixture
def stop():
    """Shuts a server down even when an assertion fails, so a leaked listener
    can't make the next test's bind flaky."""
    servers = []
    yield servers.append
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_dashboard_binds_loopback_by_default(db, cipher, stop):
    """G-36: the ops dashboard has no login of its own. On any default but
    loopback, following the quickstart publishes an unauthenticated
    'add customer' form - and every customer's contact details - on every
    interface of the machine it runs on."""
    server = serve_dashboard(db, cipher, 0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_portal_binds_loopback_by_default(db, stop):
    """The member portal has a login, but serves plain HTTP: its session
    cookie would cross the network in the clear. Reaching customers is the
    TLS proxy's job, not this socket's."""
    server = serve_portal(db, 0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_metrics_binds_loopback_by_default(stop):
    """The exposition is unauthenticated, and the styled view leaks
    operational shape - account counts, which rules are firing, which
    exchanges are misbehaving. That is reconnaissance, so it gets the same
    default as the two servers above."""
    server = Metrics.serve(0)
    stop(server)
    assert server.server_address[0] == "127.0.0.1"


def test_metrics_bind_address_is_configurable(stop):
    server = Metrics.serve(0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_dashboard_bind_address_is_configurable(db, cipher, stop):
    """Deployments that need a wider bind - a container, where Docker
    publishes by proxying to the container's interface and never to its
    loopback - must still be able to ask for one."""
    server = serve_dashboard(db, cipher, 0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_portal_bind_address_is_configurable(db, stop):
    server = serve_portal(db, 0, host="0.0.0.0")
    stop(server)
    assert server.server_address[0] == "0.0.0.0"


def test_default_dashboard_refuses_connections_from_off_box(db, cipher, stop):
    """The assertion above reads the address the server reports; this one
    proves the socket actually behaves that way. A loopback-bound listener is
    not reachable on the machine's routable address, which is the whole point
    of the default - the property a future refactor would silently lose."""
    server = serve_dashboard(db, cipher, 0)
    stop(server)
    port = server.server_address[1]

    routable = _routable_address()
    if routable is None:
        pytest.skip("host has no non-loopback IPv4 address to test against")

    with socket.socket() as probe:
        probe.settimeout(2)
        with pytest.raises(OSError):
            probe.connect((routable, port))


def _routable_address() -> str | None:
    """This machine's own non-loopback IPv4 address, without sending traffic:
    a connected UDP socket only fixes the local end of the tuple."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
            addr = s.getsockname()[0]
        except OSError:
            return None
    return None if addr.startswith("127.") else addr
